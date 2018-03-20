from scipy.spatial import distance
import numpy as np
from numpy.core.umath_tests import inner1d
import comparative_works
import pandas as pd

class ReportWriter:
    def __init__(self,
                 training_batch_handler,
                 validation_batch_handler,
                 test_batch_handler,
                 parameters,
                 report_df):
        """
        training_batch_handler:
        validation_batch_handler:
        test_batch_handler:
            The three data groups to do full hyperparam tuning on a network.
            Other models may need it.
        parameters: The list of all params.
        report_df: the final report generated by the test run. It should only contain d=0 snippets.
        
        """
        compares = comparative_works.comparative_works()
        CTRA_df = compares.CTRA_model(training_batch_handler, validation_batch_handler, test_batch_handler, parameters,
                                      report_df)
        CTRV_df = compares.CTRV_model(training_batch_handler, validation_batch_handler, test_batch_handler, parameters,
                                      report_df)
        CV_df = compares.CV_model(training_batch_handler, validation_batch_handler, test_batch_handler, parameters,
                                      report_df)
        #HMM_errors = compares.HMMGMM(training_batch_handler,validation_batch_handler,test_batch_handler,parameters,report_df)
        gaussian_df = compares.GaussianProcesses(training_batch_handler, validation_batch_handler, test_batch_handler,
                                                 parameters, report_df)
        errors_dict = {}
        errors_dict['CTRA'] = self._score_model_on_metric(CTRA_df)
        errors_dict['CTRV'] = self._score_model_on_metric(CTRV_df)
        errors_dict['CV'] = self._score_model_on_metric(CV_df)
        errors_dict['RNN'] = self._score_model_on_metric(report_df)
        consolidated_errors_dict = {}
        for name, df in errors_dict.iteritems():
            consolidated_errors_dict[name] = self._consolidate_errors(df)
            #consolidated_errors_dict[name]['model'] = name

        # for every other model:
        #   report_df = run_model
        #   model_errors = self._score...()
        # collect all scores and write a CSV or HTML or something.
        ideas = None
        self.errors_df = pd.DataFrame(consolidated_errors_dict).transpose()
        return

    def get_results(self):
        return self.errors_df

    def _consolidate_errors(self,error_df):
        metrics = list(error_df.keys())
        summarized_metrics = {}
        for metric in metrics:
            errors = error_df[metric]
            summarized_metrics[metric + " " + 'median'] = np.median(errors)
            summarized_metrics[metric + " " + 'mean'] = np.mean(errors)
            summarized_metrics[metric + " " + 'worst 5%'] = np.percentile(errors, 95)
            summarized_metrics[metric + " " + 'worst 1%'] = np.percentile(errors, 99)
        return summarized_metrics


    # Here, there are many options
    # A) metric variance. LCSS, Hausdorff, etc
    # B) Statistical variance:
        # best mean
        # best worst 5% / 1% / 0.1% <-- It took me ages to get data for a reasonable 0.1% fit!
    def _score_model_on_metric(self, report_df, metric=None):
        #scores_list = []
        track_scores = {}
        horizon_list = [5, 10, 13]#, 25, 38, 50, 63, 75]
        # horizon_dict = {}
        # for dist in horizon_list:
        #     horizon_dict[dist] = []


        for track in report_df.iterrows():

            track = track[1]

            preds = track.outputs[np.logical_not(track.trackwise_padding)]
            gts = track.decoder_sample[np.logical_not(track.trackwise_padding)]

            ### EUCLIDEAN ERROR -- Average
            euclid_error = []
            for pred, gt in zip(preds[:,0:2], gts[:,0:2]):
                # Iterates over each time-step
                euclid_error.append(distance.euclidean(pred, gt))
            ### /EUCLIDEAN

            ### HORIZON METRICS
            for dist in horizon_list:
                if dist >= len(preds):
                    continue
                euclid_error = distance.euclidean(preds[dist, 0:2], gts[dist,0:2])
                #horizon_dict[dist].append(euclid_error)
                try:
                    track_scores["horizon_steps_" + str(dist)].append(euclid_error)
                except KeyError:
                    track_scores["horizon_steps_" + str(dist)] = [euclid_error]

            # Now horizon_dict is keyed by timestep, and contains lists of distance errors
            # Mean, Median, 5% etc can now be done on those arrays.


            ### MODIFIED HAUSDORFF DISTANCE
            # Pulled shamelessly from https://github.com/sapphire008/Python/blob/master/generic/HausdorffDistance.py
            # Thanks sapphire008!
            #TODO Untested. I think it needs to be trackwise, as above
            (A, B) = (preds[:, 0:2], gts[:, 0:2])

            # Find pairwise distance
            # Very occasionally due to rounding errors it D_mat can be a small neg num, resulting in NaN
            D_mat = np.nan_to_num(np.sqrt(inner1d(A, A)[np.newaxis].T +
                            inner1d(B, B) - 2 * (np.dot(A, B.T))))
            # Calculating the forward HD: mean(min(each col))
            FHD = np.mean(np.min(D_mat, axis=1))
            # Calculating the reverse HD: mean(min(each row))
            RHD = np.mean(np.min(D_mat, axis=0))
            # Calculating mhd
            MHD = np.max(np.array([FHD, RHD]))
            ### /MHD

            try:
                track_scores['euclidean'].append(np.mean(np.array(euclid_error)))
                track_scores['MHD'].append(MHD)
            except KeyError:
                track_scores['euclidean'] = [np.mean(np.array(euclid_error))]
                track_scores['MHD'] = [MHD]


            #scores_list.append(track_scores)
        return track_scores

#TODO Make a report_df.pkl for the results, and add a if name is main here to load said cached results.