from collections import OrderedDict
import numpy as np
from gym.spaces import Box, Dict

from multiworld.envs.env_util import get_stat_in_paths, \
    create_stats_ordered_dict, get_asset_full_path
from multiworld.core.multitask_env import MultitaskEnv
from multiworld.envs.mujoco.sawyer_xyz.base import SawyerXYZEnv
import mujoco_py
from multiworld.envs.mujoco.cameras import *
from pyquaternion import Quaternion
#from mujoco_py.mjlib import mjlib

def zangle_to_quat(zangle):
    """
    :param zangle in rad
    :return: quaternion
    """
    return (Quaternion(axis=[0,1,0], angle=np.pi) * Quaternion(axis=[0, 0, -1], angle= zangle)).elements

class SawyerPushEnv( SawyerXYZEnv):
    def __init__(
            self,
            obj_low=None,
            obj_high=None,
            tasks = [{'goal': [0, 0.7, 0.02], 'obj_init_pos':[0, 0.6, 0.02]}] , 
            #tasks = None,
            goal_low=None,
            goal_high=None,
            hand_init_pos = (0, 0.4, 0.05),
            rewMode = 'posPlace',
            indicatorDist = 0.05,
            image = False,
            image_dim = 84,
            camera_name = 'robotview_zoomed',
            mpl = 150,
            hide_goal = True,
            hand_type = 'parallel_v1',
            n_tasks=2,
            **kwargs
    ):
        self.quick_init(locals()) 
        self.hand_type = hand_type       
        SawyerXYZEnv.__init__(
            self,
            hand_type = self.hand_type,
            model_name=self.model_name,
            **kwargs
        )
        if obj_low is None:
            obj_low = self.hand_low

        if goal_low is None:
            goal_low = self.hand_low

        if obj_high is None:
            obj_high = self.hand_high
        
        if goal_high is None:
            goal_high = self.hand_high

        self.camera_name = camera_name
        #self.objHeight = self.model.body_pos[-1][2]
        #assert self.objHeight != 0
        self.max_path_length = mpl
        self.image = image

        self.image_dim = image_dim
        self.task_temp = np.array(tasks)
        self.tasks = self.sample_tasks(n_tasks)
        self.num_tasks = len(tasks)
        self.rewMode = rewMode
        self.Ind = indicatorDist
        self.hand_init_pos = np.array(hand_init_pos)
        self.action_space = Box(
            np.array([-1, -1, -1]),
            np.array([1, 1, 1]),
        )
        self.hand_and_obj_space = Box(
            np.hstack((self.hand_low, obj_low)),
            np.hstack((self.hand_high, obj_high)),
        )
        self.goal_space = Box(goal_low, goal_high)
        #self.initialize_camera(sawyer_pusher_cam)
        self.info_logKeys = ['placeDist']
        self.hide_goal = hide_goal
        if self.image:
            self.set_image_obsSpace()

        else:
            self.set_state_obsSpace()

    def set_image_obsSpace(self):
        if self.camera_name == 'robotview_zoomed':
            self.observation_space = Dict([           
                    ('img_observation', Box(0, 1, (3*(48*64)+self.action_space.shape[0] , ), dtype=np.float32)),  #We append robot config to the image
                    ('state_observation', self.hand_and_obj_space), 
                ])
    def set_state_obsSpace(self):
        self.observation_space = Dict([           
                ('state_observation', self.hand_and_obj_space),
                ('state_desired_goal', self.goal_space),
                ('state_achieved_goal', self.goal_space)
            ])

    def get_goal(self):
        return {            
            'state_desired_goal': self._state_goal,
    }
      
    @property
    def model_name(self):
        #Remember to set the right limits in the base file (right line needs to be commented out)
        if self.hand_type == 'parallel_v1':
            self.reset_mocap_quat = [1,0,1,0]
            return get_asset_full_path('sawyer_xyz/sawyer_pick_and_place.xml')

        ############################# WSG GRIPPER #############################
        elif self.hand_type == 'weiss_v1':
            self.reset_mocap_quat = zangle_to_quat(np.pi/2) 
            #return get_asset_full_path('sawyer_xyz/sawyer_wsg_pickPlace_mug.xml')
            return get_asset_full_path('sawyer_xyz/sawyer_wsg_pickPlace.xml')


    def step(self, action):

      
        
        self.set_xyz_action(action[:3])
        self.do_simulation([0,0])
        self._set_goal_marker(self._state_goal)
        ob = self._get_obs()
       
        reward , reachDist, placeDist  = self.compute_reward(action, ob)
        #print(reward)
        self.curr_path_length +=1
        if self.curr_path_length == self.max_path_length:
            done = True
        else:
            done = False
        return ob, reward, done, OrderedDict({ 'reachDist':reachDist,  'placeDist': placeDist, 'epRew': reward})

    def _get_obs(self):
        

        hand = self.get_endeff_pos()
        objPos =  self.get_body_com("obj")
        flat_obs = np.concatenate((hand, objPos))

        if self.image:
            image = self.render(mode = 'nn')
            return dict(img_observation = np.concatenate([image.flatten() , hand]) , 
                        state_observation = flat_obs)

        else:
            return dict(        
                state_observation=flat_obs,
                state_desired_goal=self._state_goal,        
                state_achieved_goal=objPos,
            )

    def render(self, mode = 'human'):

        if mode == 'human':
            im_size = 500 ; norm = 1.0
            self.set_goal_visibility(visible = True)
        elif mode == 'nn':
            im_size = self.image_dim ; norm = 255.0
        elif mode == 'vis_nn':
            im_size = self.image_dim ; norm = 1.0
        else:
            raise AssertionError('Mode must be human, nn , or vis_nn')

        if self.camera_name == 'robotview_zoomed':
           
            image = self.get_image(width= im_size , height = im_size , camera_name = 'robotview_zoomed').transpose()/norm
            image = image.reshape((3, im_size, im_size))
            image = np.rot90(image, axes = (-2,-1))
            final_image = np.transpose(image , [1,2,0])
            if 'nn' in mode:
                final_image = final_image[:48 ,10 : 74,:]
            # elif 'human' in mode:
            #     final_image = final_image[:285, 60: 440,:]

        if self.hide_goal:
           self.set_goal_visibility(visible = False)
        return final_image
   
    def _get_info(self):
        pass

    def _set_goal_marker(self, goal):
        """
        
        This should be use ONLY for visualization. Use self._state_goal for
        logging, learning, etc.
        """
        self.model.site_pos[self.model.site_name2id('goal')] = (
            goal[:3]
        )

    def set_goal_visibility(self , visible = False):

        # site_id = self.model.site_name2id('goal')
        # if visible:       
        #     self.model.site_rgba[site_id][-1] = 1
        # else:
        #     self.model.site_rgba[site_id][-1] = 0
        pass


              
    def _set_obj_xyz(self, pos):
        qpos = self.data.qpos.flat.copy()
        qvel = self.data.qvel.flat.copy()
        qpos[9:12] = pos.copy()
        qvel[9:15] = 0
        self.set_state(qpos, qvel)

    def set_obs_manual(self, obs):

        assert len(obs) == 6
        handPos = obs[:3] ; objPos = obs[3:]
        self.data.set_mocap_pos('mocap', handPos)
        self.do_simulation(None)
        self._set_obj_xyz(objPos)
        

   

    def sample_tasks(self, num_tasks):

        # indices = np.random.choice(np.arange(self.num_tasks), num_tasks , replace = False)
        # return self.tasks[indices]
        indices = np.array([0, 4, 7, 3, 5, 16, 8, 10, 15, 18][:num_tasks])
        return self.task_temp[indices]

    def get_all_task_idx(self):
        return range(len(self.tasks))


    def reset_task(self, idx):
        self.change_task(self.tasks[idx])


    def change_task(self, task):
       
        if len(task['goal']) == 3:
            self._state_goal = np.array(task['goal'])
        else:
            self._state_goal = np.concatenate([task['goal'] , [0.02]])
        self._set_goal_marker(self._state_goal)

        if len(task['obj_init_pos']) == 3:
            self.obj_init_pos = np.array(task['obj_init_pos'])
        else:
            self.obj_init_pos = np.concatenate([task['obj_init_pos'] , [0.02]])
       
        
        self.origPlacingDist = np.linalg.norm( self.obj_init_pos[:2] - self._state_goal[:2])

    def reset_agent_and_object(self):

        self._reset_hand()      
        self._set_obj_xyz(self.obj_init_pos)
        self.curr_path_length = 0
        self.pickCompleted = False

    def reset_model(self, reset_arg= None):

        if reset_arg == None:
            task = self.sample_tasks(1)[0]
        else:
            assert type(reset_arg) == int
            task = self.tasks[reset_arg]

        self.current_task = task
        self.change_task(task)
        self.reset_agent_and_object()

        return self._get_obs()

    def _reset_hand(self):
        import time
        for _ in range(10):
            self.data.set_mocap_pos('mocap', self.hand_init_pos)
            self.data.set_mocap_quat('mocap', self.reset_mocap_quat)
            self.do_simulation(None, self.frame_skip)


    def get_site_pos(self, siteName):
        _id = self.model.site_names.index(siteName)
        return self.data.site_xpos[_id].copy()

    def compute_rewards(self, actions, obsBatch):
        #Required by HER-TD3
        assert isinstance(obsBatch, dict) == True
        obsList = obsBatch['state_observation']
        rewards = [self.compute_reward(action, obs)[0] for  action, obs in zip(actions, obsList)]
        return np.array(rewards)

    def compute_reward(self, actions, obs):
           
        state_obs = obs['state_observation']
        endEffPos , objPos = state_obs[0:3], state_obs[3:6] 

        placingGoal = self._state_goal

        rightFinger, leftFinger = self.get_site_pos('rightEndEffector'), self.get_site_pos('leftEndEffector')
        fingerCOM = (rightFinger + leftFinger)/2

        c1 = 1 ; c2 = 1
        reachDist = np.linalg.norm(objPos - fingerCOM)   
        placeDist = np.linalg.norm(objPos[:2] - placingGoal[:2])

        if self.rewMode == 'l2':
            reward = -reachDist - placeDist

        elif self.rewMode == 'l2Sparse':
            reward = - placeDist

        elif self.rewMode == 'l2SparseInd':
            if placeDist < self.Ind:
                reward = - placeDist
            else:
                reward = - self.origPlacingDist


        elif self.rewMode == 'posPlace':
            reward = -reachDist + 100* max(0, self.origPlacingDist - placeDist)

        return [reward, reachDist, min(placeDist, self.origPlacingDist*1.5)] 


     
    def get_diagnostics(self, paths, prefix=''):
        statistics = OrderedDict()       
        return statistics

    def log_diagnostics(self, paths = None, prefix = '', logger = None):

        # from rllab.misc import logger
        from rlkit.core import logger
        if type(paths[0]) == dict:
            if type(paths[0]) == dict:
                #For SAC
               
                #if isinstance(paths[0]['env_infos'][0] , OrderedDict):
                # for key in self.info_logKeys:
                #     nested_list = [[i[key] for i in paths[j]['env_infos']] for j in range(len(paths))]
                #     logger.record_tabular(prefix + 'last_'+key, np.mean([_list[-1] for _list in nested_list]) )

                
               
                #For TRPO
                for key in self.info_logKeys:
                    logger.record_tabular(prefix + 'last_'+key, np.mean([path['env_infos'][key][-1] for path in paths]) )

        else:
            for i in range(len(paths)):
                prefix=str(i)
                for key in self.info_logKeys:
                    logger.record_tabular(prefix + 'last_'+key, np.mean([path['env_infos'][key][-1] for path in paths[i]]) )

