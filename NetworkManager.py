# Class that handles a single instance of a network.
# Defines the training / testing state,
#  manages tensorboard handles.

# Step function should go here, that means it needs to be passed a BatchHandler,
# so that it can grab data easily
# This should also have the different test types, such as the accuracy graph
# Should it handle the entirety of crossfolding?
# I don't think so, that should go into another class maybe
import matplotlib

matplotlib.use('Agg')

import matplotlib.pyplot as plt
import tensorflow as tf
from seq2seq_model import Seq2SeqModel
import os
import numpy as np
import pandas as pd
from bokeh.plotting import figure, output_file, show, gridplot, save
from bokeh.models.widgets import Button, Paragraph, PreText
from bokeh.layouts import widgetbox
from bokeh.layouts import layout
import StringIO
import sys
import glob
import time
import matplotlib as mpl
from bokeh.models import ColumnDataSource, HoverTool, Div
import shutil

class NetworkManager:
    def __init__(self, parameters, log_file_name=None):
        self.parameters = parameters
        self.network = None
        self.batchHandler = None
        self.sess = None
        self.device = None
        self.log_file_name = log_file_name
        self.model = None
        #if parent dir is train_dir, we have a checkpoint. No
        if self.parameters['train_dir'] in os.path.basename(os.path.abspath(os.path.join(log_file_name,os.pardir))):
            self.plot_directory = os.path.join(self.parameters['master_dir'], 'plots')
            # self.network_name_string = "temp123456" # The unique network name descriptor.
            self.train_dir = os.path.join(self.parameters['master_dir'], self.parameters['train_dir'])
            self.checkpoint_dir = os.path.join(self.train_dir, os.path.basename(self.log_file_name))
            self.summaries_dir = None
        else:
            self.plot_directory = os.path.join(self.parameters['master_dir'],'plots')
            #self.network_name_string = "temp123456" # The unique network name descriptor.
            self.train_dir = os.path.join(self.parameters['master_dir'], self.parameters['train_dir'])
            self.checkpoint_dir = os.path.join(self.train_dir, os.path.basename(self.log_file_name))
            self.summaries_dir = os.path.join(self.parameters['master_dir'],'tensorboard_logs')
        self.train_writer = None
        self.val_writer = None
        self.graph_writer = None
        self.ckpt_dict = {}
        self.global_state_cached = False
        self.global_state_cache = None

        self.tensorboard_graph_summaries= []
        self.tensorboard_metric_summaries = []

        self.plot_feeds = None
        self.plot_output = None
        self.metric_feeds = None
        self.metric_output = None
        self.plt_size = (10,10) #Odd format, this is multiplied by 80 to get pixel size (blame matplotlib)

        # Silence illegal summary names INFO warning.
        # It warns that ':' is illegal. However, its in the variable.name, so I can't avoid it without
        # overly verbose code.
        #tf.logging.set_verbosity(tf.logging.ERROR)

        return

    def build_model(self):
        tf.reset_default_graph()
        self.device = tf.device(self.parameters['device'])
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.9,allow_growth=True)
        self.sess = tf.Session(config=tf.ConfigProto(allow_soft_placement=True,gpu_options=gpu_options))

        if not os.path.exists(self.train_dir):
            os.makedirs(self.train_dir)
        if not os.path.exists(self.checkpoint_dir):
            os.makedirs(self.checkpoint_dir)
        ckpt = tf.train.get_checkpoint_state(self.checkpoint_dir)
        if ckpt and tf.gfile.Exists(ckpt.model_checkpoint_path + ".index"): #TODO is +.index a hack?
            print("Reading model parameters from %s" % ckpt.model_checkpoint_path)
            #tf.train.import_meta_graph(ckpt.model_checkpoint_path + ".meta")
            #saver = tf.train.Saver()
            self.model = Seq2SeqModel(self.parameters)
            self.model.saver.restore(self.sess, ckpt.model_checkpoint_path)
        else:
            print("Created model with fresh parameters.")
            self.model = Seq2SeqModel(self.parameters)
            self.sess.run(tf.global_variables_initializer())

        if self.summaries_dir is not None:
            self.train_writer = tf.summary.FileWriter(os.path.join(self.summaries_dir,self.log_file_name+'train'),
                                                       graph=self.sess.graph)
            self.val_writer = tf.summary.FileWriter(os.path.join(self.summaries_dir,self.log_file_name+'val'),
                                                     graph=self.sess.graph)
            self.graph_writer = tf.summary.FileWriter(os.path.join(self.summaries_dir, self.log_file_name + 'graph'),
                                                      graph=self.sess.graph)

        return

    def log_graphs_to_tensorboard(self,graphs):
        img_values = []
        for i in range(len(graphs)):
            img_summary = tf.Summary.Image(encoded_image_string=graphs[i],height=self.plt_size[1],width=self.plt_size[0])
            summary_value = tf.Summary.Value(tag=str(i),image=img_summary)
            img_values.append(summary_value)

        summary_str = tf.Summary(value=img_values)

        self.graph_writer.add_summary(summary_str, self.model.global_step.eval(session=self.sess))
        return

    # Logs a list of floats that are passed as args into Tensorboard, so they can be graphed over time.
    def log_metric_to_tensorboard(self,metrics):
        m_values = []
        for i in range(len(metrics)):
            summary_value = tf.Summary.Value(tag="metric_"+str(i),simple_value=metrics[i])
            m_values.append(summary_value)
        summary_str = tf.Summary(value=m_values)
        self.graph_writer.add_summary(summary_str, self.model.global_step.eval(session=self.sess))
        return

    def get_global_step(self):
        if self.global_state_cached == True:
            return self.global_state_cache
        else:
            self.global_state_cache = self.model.global_step.eval(session=self.sess)
            self.global_state_cached = True
        return self.global_state_cache

    def get_learning_rate(self):
        return self.model.learning_rate.eval(session=self.sess)

    def decay_learning_rate(self):
        self.sess.run(self.model.learning_rate_decay_op)
        return

    def run_training_step(self, X, Y, weights, train_model, summary_writer=None):
        self.global_state_cached = False
        return self.model.step(self.sess, X, Y, weights, train_model, summary_writer=summary_writer)

    def draw_bokeh_linear_plot(self,graph_results):
        plot_titles = graph_results['origin'].unique()
        plots = []

        for origin in plot_titles:
            if self.parameters['data_format'] == 'legacy':
                if os.path.exists("QDA/" + origin + ".npy"):
                    QDA_data = np.load("QDA/" + origin + ".npy")
                QDA_mean = QDA_data[0] / 100
                QDA_meanpstd = QDA_data[1] / 100
                QDA_meanmstd = QDA_data[2] / 100
                QDA_range = np.array(range(len(QDA_mean)))
                QDA_range -= 40

            plt_title = 'Accuracy as measured relative to 20m mark. Averaged over all tracks'
            # plot 1
            dataset = graph_results[graph_results['origin'] == origin]
            x_data = []
            y_data = []
            tp_data = []
            fp_data = []
            fn_data = []
            try:
                f1_labels = True
                graph_results['f1_score']
                for range_val in np.unique(dataset['distance']):
                    data_at_range = dataset[dataset['distance'] == range_val]
                    x_data.append(range_val)
                    y_data.append(data_at_range['f1_score'][0])
                    tp_data.append(data_at_range['true_positive'][0])
                    fp_data.append(data_at_range['false_positive'][0])
                    fn_data.append(data_at_range['false_negative'][0])
            except KeyError:
                f1_labels = False
                for range_val in np.unique(dataset['d_thresh']):
                    # If I group by track number here, I can get a collection of accuracy scores
                    # and therefore a std dev
                    data_at_range = dataset[dataset['d_thresh'] == range_val]
                    acc = np.average(np.equal(data_at_range['output_idxs'],
                                              data_at_range['destination_vec']))
                    x_data.append(range_val)
                    y_data.append(acc)

            p1 = figure(title='Origin: ' + origin, x_axis_label='Dis from Ref Line (m)', y_axis_label='Acc.',
                        plot_width=500, plot_height=500)  # ~half a 1080p screen
            if not f1_labels:
                p1.line(x_data, y_data, legend="Acc. RNN", line_width=2, color='green')
            else:
                p1.line(x_data, y_data, legend="F1 Score RNN", line_width=2, color='green')
                p1.line(x_data, tp_data, legend="True Positive Percent RNN", line_width=2, color='yellow')
                p1.line(x_data, fp_data, legend="False Positive Percent RNN", line_width=2, color='orange')
                p1.line(x_data, fn_data, legend="False Negative Percent RNN", line_width=2, color='blue')

            if self.parameters['data_format'] == 'legacy':
                p1.line(QDA_range, QDA_mean, legend="Acc. QDA", line_width=2, color='red', line_alpha=1)
            # p1.line(QDA_range, QDA_meanmstd, line_width=2, color='red', line_alpha=0.5)
            # p1.line(QDA_range, QDA_meanpstd, line_width=2, color='red', line_alpha=0.5)
            # p1.line(bbox_range, loss, legend="Loss.", line_width=2, color='blue')
            # p1.line(bbox_range, output_gen_plt[:, 1], legend="Generated Output.", line_width=2, color='red')
            p1.legend.location = "bottom_right"
            plots.append(p1)
        return plots

    def draw_bokeh_topographical_plot(self, results_per_dis_df, batch_handler):
        image_filename = 'leith-croydon.png'
        if not os.path.exists(os.path.join(self.plot_directory,image_filename)):
            shutil.copy(os.path.join('images',image_filename),os.path.join(self.plot_directory,image_filename))

        plots = []
        batch_handler.data_pool.track_idx.unique()
        data_pool = batch_handler.data_pool

        longest_track_dict = {}
        for track_class in data_pool['track_class'].unique():
            #THIS DOES NOT RETURN THE LONGEST TRACK.
            longest_track_dict[track_class] = data_pool[data_pool['track_class'] == track_class]['track_idx'].mode()

        for track_class, track_idx in longest_track_dict.iteritems():
            if "Object_Y" not in self.parameters['ibeo_data_columns'][1]:
                print "You broke my hack! X and Y need to be the first encoder items."
                quit()
            track_idx = track_idx[0]

            x_list=[]
            y_list=[]
            acc_list=[]

            track_class_df = results_per_dis_df[(results_per_dis_df.track_class==track_class)]
            long_track_df = track_class_df[track_class_df['track_idx']==track_idx]

            #Iterate over every row in long_DF
            #Convert Long_DF to dict.
            long_track_dict = long_track_df.reset_index(drop=True).to_dict('list')
            # calculate and assign
            for d_thresh in long_track_dict['d_thresh']:
                data_at_range = track_class_df[track_class_df['d_thresh'] == d_thresh]
                acc = np.average(np.equal(data_at_range['output_idxs'],
                                          data_at_range['destination_vec']))
                acc_list.append(acc)
                #x_list.append(long_track_df[long_track_df['d_thresh']==d_thresh].iloc[0]['Object_X'])
                #y_list.append(long_track_df[long_track_df['d_thresh'] == d_thresh].iloc[0]['Object_Y'])
            long_track_dict['Accuracy'] = acc_list
            long_track_dict['colours'] = [
                "#%02x%02x%02x" % (int(r), int(g), int(b)) for r, g, b, _ in
                255 * mpl.cm.viridis(mpl.colors.Normalize()(acc_list))
            ]
            tooltips = []
            for name, value in long_track_dict.iteritems():
                if name in ['Accuracy','Object_X','Object_Y','distance','distance_to_exit','track_idx','uniqueId','Timestamp','AbsVelocity']:
                    tooltips.append(tuple([name, "@" + name])) # X,Y Vel, Distance to xx, accuracy.
            hover = HoverTool(tooltips=tooltips)
            p = figure(plot_height=600, plot_width=700, title=track_class, x_range=(-40, 20), y_range=(-30, 30),
                       tools=[hover,'pan','wheel_zoom','box_zoom','reset','resize'])
            #Angle is in radians, rotates around anchor
            #p.image_url([image_filename], x=-89, y=35.4, w=147.45, h=77.0, angle=0, anchor='top_left'
            p.image_url([image_filename], x=-15.275, y=-3.1, w=147.45, h=77.0, angle=0,
                        anchor='center', global_alpha=0.7)
            plot_source = ColumnDataSource(data=long_track_dict)
            p.circle(x="Object_X", y='Object_Y', size=4, fill_color="colours", fill_alpha=0.6,line_color="colours",
                     source=plot_source)
            plots.append(p)
            ideas=None

        return plots

    def draw_html_graphs(self, batch_handler):

        if not os.path.exists(self.plot_directory):
            os.makedirs(self.plot_directory)
        plt_path = os.path.join(self.plot_directory, os.path.basename(self.log_file_name) + '.html')
        # If I am running this many times, make new filenames
        if os.path.exists(plt_path):
            path_idx = 1
            while os.path.exists(plt_path):
                plt_path = os.path.join(self.plot_directory,
                                        os.path.basename(self.log_file_name) + "-%02d" % path_idx + '.html')
                path_idx += 1

        output_file(plt_path)

        results_per_dis = self.compute_result_per_dis(batch_handler)
        dis_f1_report = self.compute_distance_f1_report(results_per_dis)
        top_plots = self.draw_bokeh_topographical_plot(results_per_dis, batch_handler)
        linear_plots = self.draw_bokeh_linear_plot(results_per_dis)

        #topographical_plots = self.draw_bokeh_topographical_plot(graph_results)

        # Dump all the metadata to a big string.
        label_str = ""
        for key, value in self.parameters.iteritems():
            label_str += str(key) + ': ' + str(value) + "\r\n"
        paragraph_1 = PreText(text=label_str)


        l = layout([top_plots, linear_plots, [widgetbox(paragraph_1, width=800)]])
        save(l)
        # show(widgetbox(button_1, width=300))

        return

    def draw_png_graphs_perf_dist(self, graph_results):
        fig_dir = self.plot_directory + "_img"
        if not os.path.exists(fig_dir):
            os.makedirs(fig_dir)

        graph_list = []

        plot_titles = graph_results['origin'].unique()
        for origin in plot_titles:
            if self.parameters['data_format'] == 'legacy':
                if os.path.exists("QDA/" + origin + ".npy"):
                    QDA_data = np.load("QDA/" + origin + ".npy")
                QDA_mean = QDA_data[0] / 100
                QDA_meanpstd = QDA_data[1] / 100
                QDA_meanmstd = QDA_data[2] / 100
                QDA_range = np.array(range(len(QDA_mean)))
                QDA_range -= 40
            dataset = graph_results[graph_results['origin'] == origin]
            x_data = []
            y_data = []
            tp_data = []
            fp_data = []
            fn_data = []
            try:
                f1_labels = True
                graph_results['f1_score']
                for range_val in np.unique(dataset['distance']):
                    data_at_range = dataset[dataset['distance'] == range_val]
                    x_data.append(range_val)
                    y_data.append(data_at_range['f1_score'][0])
                    tp_data.append(data_at_range['true_positive'][0])
                    fp_data.append(data_at_range['false_positive'][0])
                    fn_data.append(data_at_range['false_negative'][0])

            except KeyError:
                f1_labels = False
                for range_val in np.unique(dataset['d_thresh']):
                    # If I group by track number here, I can get a collection of accuracy scores
                    # and therefore a std dev
                    data_at_range = dataset[dataset['d_thresh'] == range_val]
                    acc = np.average(np.equal(data_at_range['output_idxs'],
                                              data_at_range['destination_vec']))
                    x_data.append(range_val)
                    y_data.append(acc)

            legend_str = []
            fig = plt.figure(figsize=self.plt_size)


            if self.parameters['data_format'] == 'legacy':
                plt.plot(QDA_range, QDA_mean,'r-')
                legend_str.append(['Acc. QDA'])
            plt.title('Origin: ' + origin)
            plt.xlabel('Distance from Ref Line (m)')
            if not f1_labels:
                plt.plot(x_data, y_data, 'g-', label=origin)
                plt.ylabel('Accuracy')
                legend_str.append(['Acc. RNN'])
            else:
                plt.plot(x_data, y_data, 'g-', label=origin)
                plt.ylabel('Value')
                legend_str.append(['F1 Score'])
                plt.plot(x_data, fp_data,'r-')
                legend_str.append(['False Positive Percent'])
                plt.plot(x_data, fn_data,'b-')
                legend_str.append(['False Negative Percent'])
                plt.plot(x_data, tp_data,'c-')
                legend_str.append(['True Positive Percent'])
            plt.legend(legend_str, loc='upper left')

            fig_path = os.path.join(self.plot_directory + "_img", self.log_file_name + '-' +
                                    str(self.get_global_step()) + '-' + origin+ '.png')
            plt.savefig(fig_path, bbox_inches='tight')

            fig.canvas.draw()
            fig_s = fig.canvas.tostring_rgb()
            fig_data = np.fromstring(fig_s,np.uint8)
            fig_data = fig_data.reshape(fig.canvas.get_width_height()[::-1] + (3,))
            s = StringIO.StringIO()
            plt.imsave(s, fig_data,format='png')
            fig_data = s.getvalue()
            graph_list.append(fig_data)
            plt.close()

        return graph_list

    # def _pick_class(self,distribution):
    #     threshold = 0.95
    #     pop = np.sum(distribution)
    #     distribution = distribution / pop
    #     (dist_results.iloc[0]['dest_1_hot'].astype(int) == (dist_results.iloc[0]['output_pop'][0] > 95).astype(
    #         int)).all()
    #
    #     return

    def compute_distance_f1_report(self, dist_results):
        # Maybe at the end of training I want a ROC curve on the confidence threshold.
        # Right now I want an F1 score with a default threshold.

        # FIXME can I get classes a better way?
        classes = dist_results.origin.unique()
        f1_df_list = []

        # Declare class based on output_pop
        population = np.sum(dist_results.iloc[0]['output_pop'])
        dist_results = dist_results.assign(norm_pop=dist_results['output_pop']/population)
        class_threshold = 0.95
        dist_results = dist_results.assign(chosen_pop=pd.Series([(x > class_threshold).astype(float) for x in dist_results['norm_pop']]))
        dist_results['correct_classification'] = \
            dist_results.apply(lambda x: True if (x['chosen_pop'] == x['dest_1_hot']).all() else False, axis=1)
        dist_results['any_classification'] = \
            dist_results.apply(lambda x: True if (x['chosen_pop']).any() else False, axis=1)

        for distance in dist_results['d_thresh'].unique():
            distance_set = dist_results[dist_results['d_thresh'] == distance]

            #f1 score calc needs:
            # True positive = number correctly classified
            # False Positive = Number incorrectly classified as this class
            # False Negative = Number without class, or wrong class.
            for origin in classes:
                origin_subset = distance_set[distance_set['origin'] == origin]
                origin_dist = len(origin_subset)
                TP = len(origin_subset[origin_subset['correct_classification'] == True])
                FN = len(origin_subset[origin_subset['any_classification'] == False])
                FP = origin_dist - TP - FN
                #print ("Dis: %3.2fm Dest: %5s TP %2d FN %2d FP %2d" % (distance, dest, TP, FN, FP))
                try:
                    f1 = 2*TP / float(2*TP + FP + FN)
                except ZeroDivisionError:
                    f1 = 0.0
                n_tracks = len(origin_subset)
                if n_tracks == 0: n_tracks = 1 # If this is zeo, then FP,FN and TP are zero as well..
                f1_df_list.append(pd.DataFrame({"origin": origin,
                                                "distance": distance,
                                                "f1_score": f1,
                                                "true_positive": TP / n_tracks,
                                                "false_positive": FP / n_tracks,
                                                "false_negative": FN / n_tracks,
                                                "n_tracks": n_tracks
                                                },index=[0]))
        f1_df = pd.concat(f1_df_list)
        return f1_df

    # This function needs the validation batch (or test batch)
    # This is to be refactored as a report writer, that is done every n minutes
    # ~5 min for soak, ~20 min for long
    # This system should then do a ROC analysis at each distance we care about
    def compute_result_per_dis(self, batch_handler, plot=True):
        # Legacy plot needs exactly one data point per meter.
        min_plot = max([-15,int(0.5+min(batch_handler.data_pool['distance']))])
        max_plot = min([60,int(max(batch_handler.data_pool['distance']))])
        print "min_plot: " + str(min_plot) + ' max_plot: ' + str(max_plot)
        if plot:
            bbox_range_plot = np.arange(min_plot,max_plot,1).tolist()
        else:
            bbox_range_plot = np.arange(min_plot, max_plot, 0.5).tolist()

        graph_results = []
        # This could be optimized.
        # If the batch size is larger than twice a sequential minibatch
        # I could run two distances per step.
        print ""
        #TEMP
        if True:
        #batch_handler.generate_distance_minibatches(bbox_range_plot)
        # for d in bbox_range_plot:
        #     sys.stdout.write("\rGenerating distance report: %03.1fm %10s" % (d, ''))
        #     sys.stdout.flush()
            # Set d_thresh
            # Do it in a loop in case batch_size < num_val_tracks
            dis_thresh_time = time.time()
            #batch_handler.set_distance_threshold(d)
            batch_handler.set_distance_threshold_ranges(bbox_range_plot)
            #print "Time to set dis thresh: " + str (time.time() - dis_thresh_time)
            batch_complete = False

            batch_time = time.time()
            busy_indicator = ['-', '\\', '|', '/']
            batch_counter = 0
            while not batch_complete:
                #print "Running batch"
                sys.stdout.write("\rWriting distance report...%s" % busy_indicator[batch_counter%len(busy_indicator)])
                sys.stdout.flush()
                mini_batch_frame,batch_complete = batch_handler.get_sequential_minibatch()
                #TODO check if mini_batch_frame is empty here. If I have no data at all for this range.
                if mini_batch_frame is None:
                    break
                val_x, _, val_weights, val_y = \
                    batch_handler.format_minibatch_data(mini_batch_frame['encoder_sample'],
                                                        mini_batch_frame['dest_1_hot'],
                                                        mini_batch_frame['padding'])
                valid_data = np.logical_not(mini_batch_frame['padding'].values)
                #print "Time to get minibatch: " + str(time.time()-batch_time)

                #TODO Param this:
                output_samples = []
                num_samples = 1 #FIXME squeeze breaks if num_samples is 1
                for _ in range(num_samples):
                    acc, loss, outputs = self.model.step(self.sess, val_x, val_y,
                                                         val_weights, False, summary_writer=None)
                    # Do a straight comparison between val_y and outputs.
                    #output_idxs = np.argmax(outputs[0][valid_data], axis=1)
                    output_samples.append(outputs)

                #Get a population count of what the network thinks.
                output_samples_arr = np.array(output_samples).squeeze(axis=1)
                output_1_hot = np.eye(output_samples_arr.shape[2])[np.argmax(output_samples_arr, axis=2)]
                output_pop = np.sum(output_1_hot,axis=0)

                #Get a percentage of population in the correct class.
                y_idxs = np.argmax(val_y,axis=2).squeeze()
                acc_pop = output_pop[0][y_idxs]/num_samples

                # Drop all results that are just padding to make the minibatch square.
                output_pop = output_pop[valid_data]
                acc_pop = acc_pop[valid_data]
                mini_batch_frame = mini_batch_frame[mini_batch_frame['padding'] == False]

                # TODO Repeal and replace this qualifier.
                # Compute max pop. Assign it to idx for now. LEGACY FUNCTION
                output_idxs = np.argmax(output_pop,axis=1)
                mini_batch_frame = mini_batch_frame.assign(output_idxs=output_idxs)
                mini_batch_frame = mini_batch_frame.assign(acc_pop=acc_pop)
                mini_batch_frame = mini_batch_frame.assign(output_pop=pd.Series([x for x in output_pop],dtype=object))
                #mini_batch_frame = mini_batch_frame.assign(d_thresh=np.repeat(d,len(mini_batch_frame)))

                graph_results.append(mini_batch_frame)
                batch_counter += 1
            #print "Time to run dis batches: " + str(time.time() - batch_time)

        #Concat once only, much faster
        graph_results_frame = pd.concat(graph_results)

        # Reset handler
        batch_handler.set_distance_threshold(None)
        batch_handler.set_distance_threshold_ranges(None)

        return graph_results_frame

    def evaluate_metric(self,results):

        d_array = []
        for origin in results['origin'].unique():
            # Generate the set of all distances that are not 100% accurate (i.e. they have a incorrect classification)
            # Remove from the set of all distances, creating only a set of distances with a perfect score
            # Return lowest number (the earliest result)
            dis_unique = results['d_thresh'].unique()
            dist_delta = dis_unique[1] - dis_unique[0]
            reduced_df = results[results['origin']==origin]
            perfect_dist = np.setdiff1d(dis_unique,
                                        reduced_df[
                                            reduced_df['destination_vec']!=reduced_df['output_idxs']
                                        ].d_thresh.unique())
            #If we got none right OR the final value is incorrect (rare case)
            if (len(perfect_dist) < 2) or\
                    (perfect_dist[-1] != dis_unique[-1]):
                d_array.append(np.max(dis_unique))
            else:
                # Find the end of the continuous sequence at the end of the graph
                # Return this point
                for i in reversed(range(1,len(perfect_dist))):
                    if perfect_dist[i] - perfect_dist[i-1] != dist_delta:
                        break
                perfect_dist_threshold = perfect_dist[i]
                d_array.append(np.min(perfect_dist_threshold))

        return d_array, results['origin'].unique()

    # Function that passes the entire validation dataset through the network once and only once.
    # Return cumulative accuracy, loss
    def run_validation(self, batch_handler, summary_writer=None,quick=False):
        batch_complete = False
        batch_losses = []
        total_correct = 0
        total_valid = 0
        while not batch_complete:
            #val_x, val_y, val_weights, pad_vector, batch_complete = batch_handler.get_sequential_minibatch()
            if 'QUICK_VALBATCH' in os.environ or quick:
                # Run one regular batch. Debug mode takes longer, and there are ~30,000 val samples
                mini_batch_frame = batch_handler.get_minibatch()
                batch_complete = True
                #print "Debug active, valdating with random sample, not whole batch"
            else:
                mini_batch_frame,batch_complete = batch_handler.get_sequential_minibatch()

            val_x, _, val_weights, val_y = batch_handler.format_minibatch_data(mini_batch_frame['encoder_sample'],
                                                                               mini_batch_frame['dest_1_hot'],
                                                                               mini_batch_frame['padding'])
            valid_data = np.logical_not(mini_batch_frame['padding'].values)
            acc, loss, outputs = self.model.step(self.sess, val_x, val_y, val_weights, False, summary_writer=summary_writer)

            output_idxs = np.argmax(outputs[0][valid_data], axis=1)
            y_idxs = np.argmax(np.array(val_y)[0][valid_data], axis=1)
            num_correct = np.sum(np.equal(output_idxs,y_idxs)*1)
            num_valid = np.sum(valid_data*1)
            total_correct += num_correct
            total_valid += num_valid
            batch_losses.append(loss)

        batch_acc = np.float32(total_correct) / np.float32(total_valid)

        return batch_acc, np.average(batch_losses), None

    # Checkpoints model. Adds path to global dict lookup
    def checkpoint_model(self):
        self.ckpt_dict[self.get_global_step()] = \
            self.model.saver.save(self.sess, os.path.join(self.checkpoint_dir, 'model-chkpt'),
                              global_step=self.get_global_step())

    def load_from_checkpoint(self,g_step=None):
        ckpt = tf.train.get_checkpoint_state(self.checkpoint_dir)
        if g_step:
            ckpt_dir = self.ckpt_dict[g_step]
        else:
            ckpt_dir = ckpt.model_checkpoint_path
        if ckpt and ckpt_dir:
            print("Reading model parameters from %s" % ckpt_dir)
            self.model.saver.restore(self.sess, ckpt_dir)
        return

    def clean_checkpoint_dir(self,g_step=None):
        ckpt = tf.train.get_checkpoint_state(self.checkpoint_dir)
        for checkpoint in ckpt.all_model_checkpoint_paths:
            if (g_step is not None and
                checkpoint != self.ckpt_dict[g_step]):
                [os.remove(file) for file in glob.glob(checkpoint + "*")]


