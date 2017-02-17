from watchdog.events import PatternMatchingEventHandler
from slice_processor import SliceProcessor, proc_epi_slice
import multiprocessing as mp
import numpy as np
from scipy.signal import detrend
import nibabel as nib
import os, time

class SmokerWatcher(PatternMatchingEventHandler):
    def __init__(self, config):
        PatternMatchingEventHandler.__init__(self, 
            patterns=['*MB*.imgdat'],
            ignore_patterns=[],
            ignore_directories=True)
        self.pool = mp.Pool()
        self.trials = config['trials-per-run']
        self.zscore_trs = config['zscore-trs']
        self.cue_trs = config['cue-trs']
        self.wait_trs = config['wait-trs']
        self.feedback_trs = config['feedback-trs']
        self.iti_trs = config['iti-trs']
        self.trial_trs = self.cue_trs+self.wait_trs+self.feedback_trs+self.iti_trs
        self.moving_avg_trs = config['moving-avg-trs']
        # calc which reps are 'special' feedback calculation ones
        self.trs_to_score_calc = self.cue_trs+self.wait_trs-1
        self.feedback_calc_trs = (self.zscore_trs+self.trs_to_score_calc
                                  +np.arange(self.trials)*self.trial_trs-1)
        self.run_count = 0

        self.ref_dir = config['ref-dir']
        self.target_class = config['target-class']
        self.proc_dir = config['proc-dir']
        self.serve_dir = config'serve-dir']
        self.rfi_file = self.ref_dir+'/rfi.nii'
        self.clf_file = self.ref_dir+'/clf.nii'
        self.load_clf(self.clf_file)
        self.run_trs = self.zscore_trs+self.trials*self.trial_trs
        self.reset_img_arrays()

    def load_clf(self, filename):
        self.clf_img = nib.load(filename).get_data()
        self.slice_dims = (self.clf_img.shape[0],self.clf_img.shape[1])
        self.num_slices = self.clf_img.shape[2]
        self.clf_voxels = np.where(clf_img!=0)
        self.clf_voxels = np.ascontiguousarray(self.clf_voxels)
        self.clf_voxels = np.ascontiguousarray(self.clf_voxels[0:3,:].T)
        self.roi_voxels = np.unique(self.clf_voxels.view([('', self.clf_voxels.dtype)]*self.clf_voxels.shape[1]))
        self.roi_voxels = self.roi_voxels.view(self.clf_voxels.dtype).reshape((self.roi_voxels.shape[0], self.clf_voxels.shape[1]))
        self.num_roi_voxels = np.shape(self.roi_voxels)[0]
        self.classifier = np.zeros(self.num_roi_voxels,np.shape(self.clf_img)[3])
        for out_class in range(np.shape(self.clf_img)[3]):
            self.classifier[:,out_class] = map_voxels_to_roi(self.clf_img[:,:,:,out_class])

    def reset_img_arrays(self):
        self.img_status_array = np.zeros(self.run_trs)
        self.raw_img_array = np.zeros((self.slice_dims[0],self.slice_dims[1],
            self.num_slices,self.run_trs),dtype=np.uint16)
        self.raw_roi_array = np.zeros((self.num_roi_voxels,self.run_trs))
        self.trial_count = 0
        self.zscore_calc = False
        self.voxel_sigmas = np.zeros(self.num_roi_voxels)

    def on_created(self, event):
        img_file = event.src_path.rsplit('/')[-1]
        rep = int(img_file.split('R')[1].split('-')[0])-1
        slc = int(img_file.split('S')[1].split('.')[0])-1
        with open(event.src_path) as f:
            self.raw_img_array[:,:,slc,rep] = np.fromfile(f,dtype=np.uint16).reshape(self.slice_dims)
        self.img_status_array[rep] += 1
        if self.img_status_array[rep] == self.num_slices:
            self.sp_pool.apply_async(func = process_volume,
                args = (self.raw_img_array[:,:,:,rep],self.roi_voxels,rep,self.rfi_file,self.proc_dir),
                callback = self.save_processed_roi)

    def save_processed_roi(self, (roi_data,rep)):
        self.raw_roi_array[:,rep] = roi_data
        if rep == (self.zscores_trs-1):
            self.voxel_sigmas = np.sqrt(np.var(self.raw_roi_array,1))
        if rep in self.feedback_calc_trs:
            detrend_roi_array = detrend(self.raw_roi_array[:,:rep+1],1)
            zscore_avg_roi = np.mean(detrend_roi_array[:,-self.moving_avg_trs:],1)/self.voxel_sigmas
            clf_out_raw = np.matmul(zscore_avg_roi,self.classifier)
            clf_out_softmax = np.exp(clf_out_raw)/sum(np.exp(raw_clf))
            target_clf_out = clf_out_softmax[self.target_class-1]
            out_file = self.serve_dir + '/.txt'
            with open(out_file,'w') as f:
                f.write(str(target_clf_out))
        if rep == (self.run_trs-1):
            self.reset_for_next_run()

    def reset_for_next_run(self):
        self.run_count += 1
        # move served files to archive?
        self.reset_img_arrays()

def process_volume(raw_img, roi_voxels, rep, rfi_file, proc_dir):
    temp_file = proc_dir + '/temp_img_' + str(rep) + '.nii.gz'
    mc_file = proc_dir + '/temp_img_mc_' + str(rep) + '.nii.gz'
    nib.save(nib.Nifti1Image(raw_img, np.eye(4)), temp_file)
    command = 'mcflirt -in ' + temp_file + ' -dof 6 -reffile ' + rfi_file + ' -out ' + mc_file
    os.system(command)
    while not(os.path.isfile(mc_file)):
        pass
    roi_data = map_voxels_to_roi(nib.load(mc_file).get_data(),roi_voxels)
    return (roi_data, rep)

def map_voxels_to_roi(img, roi_voxels):
    out_roi = np.zeros(roi_voxels.shape[0])
    for voxel in range(roi_voxels.shape[0])
        out_roi[voxel] = img[roi_voxels[voxel,0],roi_voxels[voxel,1],roi_voxels[voxel,2]]
    return out_roi