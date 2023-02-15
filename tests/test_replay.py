#!/usr/bin/env python

from __future__ import print_function
from ipaddress import v4_int_to_packed
import os
import json
import math
import pickle
import shutil
from os import listdir
from os.path import join, abspath, dirname, isdir, isfile, basename
from tabnanny import verbose
from config import EXP_PATH, MAMAO_DATA_PATH
import numpy as np
import random
import time
import sys
from PIL import Image

from pybullet_tools.bullet_utils import query_yes_no, get_datetime, nice
from pybullet_tools.utils import reset_simulation, VideoSaver, wait_unlocked, draw_aabb, get_aabb
from lisdf_tools.lisdf_loader import load_lisdf_pybullet, pddlstream_from_dir
from lisdf_tools.lisdf_planning import pddl_to_init_goal, Problem
from lisdf_tools.image_utils import make_composed_image_multiple_episodes, images_to_gif
from isaac_tools.gym_utils import save_gym_run, interpolate_camera_pose
from world_builder.actions import apply_actions

from mamao_tools.data_utils import get_plan, get_body_map, get_multiple_solutions, \
    add_to_planning_config, load_planning_config, exist_instance, \
    check_unrealistic_placement_z

from test_utils import process_all_tasks, copy_dir_for_process, get_base_parser, \
    get_sample_envs_for_rss

USE_GYM = False
SAVE_COMPOSED_JPG = False
SAVE_GIF = True
SAVE_JPG = True or SAVE_COMPOSED_JPG or SAVE_GIF
PREVIEW_SCENE = False

CHECK_COLLISIONS = False
CFREE_RANGE = 0.1
VISUALIZE_COLLISIONS = False

SAVE_MP4 = False
STEP_BY_STEP = False
AUTO_PLAY = True
EVALUATE_QUALITY = False
PARALLEL = SAVE_JPG and not PREVIEW_SCENE and False  ## and not CHECK_COLLISIONS

SKIP_IF_PROCESSED_RECENTLY = False
CHECK_TIME = 1674417578

CAMERA_KWARGS = None
LIGHT_CONF = None
CAMERA_MOVEMENT = None
GIVEN_PATH = None
GIVEN_DIR = None
TASK_NAME = 'mm_storage'
CASES = None
# CASES = ['16']  ##
# CASES = get_sample_envs_for_rss(task_name=TASK_NAME, count=None)

if GIVEN_PATH:
    VISUALIZE_COLLISIONS = True
    PARALLEL = False
if GIVEN_PATH is not None and 'rerun' in GIVEN_PATH:
    SAVE_JPG = False
    SAVE_COMPOSED_JPG = False
    SAVE_GIF = False

parser = get_base_parser(task_name=TASK_NAME, parallel=PARALLEL, use_viewer=True)
parser.add_argument('--path', type=str, default=GIVEN_PATH)
parser.add_argument('--step', action='store_true', default=STEP_BY_STEP)
args = parser.parse_args()

GIVEN_PATH = args.path
STEP_BY_STEP = args.step
#####################################


def get_pkl_run(run_dir, verbose=True):
    if basename(run_dir) == 'collisions.pkl':
        pkl_file = run_dir
        run_dir = dirname(run_dir)
    else:
        pkl_file = 'commands.pkl'
        if run_dir.endswith('.pkl'):
            pkl_file = basename(run_dir)
            run_dir = run_dir[:-len(pkl_file) - 1]
            rerun_dir = basename(run_dir)
            run_dir = run_dir[:-len(rerun_dir) - 1]
            pkl_file = join(rerun_dir, pkl_file)

    exp_dir = copy_dir_for_process(run_dir, tag='replaying', verbose=verbose)
    if basename(pkl_file) == 'collisions.pkl':
        plan = None
    elif basename(pkl_file) != 'commands.pkl':
        plan_json = join(run_dir, pkl_file).replace('commands', 'plan').replace('.pkl', '.json')
        plan = get_plan(run_dir, plan_json=plan_json)
    else:
        ## if there are reran versions
        plan = get_plan(run_dir, skip_multiple_plans=True)
    commands = pickle.load(open(join(exp_dir, pkl_file), "rb"))
    return exp_dir, run_dir, commands, plan


def check_if_exist_rerun(run_dir, world, commands, plan):
    indices = world.get_indices()
    multiple_solutions = get_multiple_solutions(run_dir, indices=indices, commands_too=True)
    if len(multiple_solutions) > 1:
        plan, path = multiple_solutions[0]
        commands_file = join(path, 'commands.pkl')
        if not isfile(commands_file):
            return None
        commands = pickle.load(open(commands_file, "rb"))
    return commands, plan


def swap_microwave(run_dir, verbose=False):
    exp_dir = copy_dir_for_process(run_dir, tag='swapping microwave', verbose=verbose)
    world = load_lisdf_pybullet(exp_dir, use_gui=not USE_GYM, verbose=False)


def run_one(run_dir_ori, task_name=TASK_NAME, save_gif=True, save_mp4=SAVE_MP4, width=1440, height=1120, fx=600,
            camera_point=(8.5, 2.5, 3), target_point=(0, 2.5, 0)):

    verbose = not SAVE_JPG

    exp_dir, run_dir, commands, plan = get_pkl_run(run_dir_ori, verbose=verbose)

    # load_lisdf_synthesizer(exp_dir)
    larger_world = 'rerun' in run_dir_ori and '/tt_' in run_dir_ori
    world = load_lisdf_pybullet(exp_dir, use_gui=not USE_GYM, width=width, height=height,
                                verbose=False, larger_world=larger_world)
    aabb = get_aabb(world.name_to_body['counter#1'])
    print(nice(aabb))
    print(aabb.upper[0])
    # if not USE_GYM and GIVEN_PATH is not None:
    #     wait_unlocked()
    problem = Problem(world)
    if verbose:
        world.summarize_all_objects()
    body_map = get_body_map(run_dir, world, larger=False)
    result = check_if_exist_rerun(run_dir, world, commands, plan)
    if result is None:
        print(run_dir_ori, 'does not have rerun commands.pkl')
        reset_simulation()
        shutil.rmtree(exp_dir)
        return
    commands, plan = result

    ## -----------------------------------------------------------
    # artichoke = world.safely_get_body_from_name('veggiepotato')
    # draw_aabb(get_aabb(artichoke))
    # microwave = world.safely_get_body_from_name('microwave')
    # draw_aabb(get_aabb(microwave))
    # wait_unlocked()
    ## -----------------------------------------------------------

    ## save the initial scene image in pybullet
    zoomin_kwargs = dict(width=width//4, height=height//4, fx=fx//2)
    if not CHECK_COLLISIONS and SAVE_JPG:
        viz_dir = join(run_dir_ori, f'images')
        for sud in range(len(world.camera_kwargs)):
            viz_dir = join(run_dir_ori, f'zoomin_{sud}'.replace('_0', ''))
            world.add_camera(viz_dir, img_dir=viz_dir, **zoomin_kwargs, **world.camera_kwargs[sud])
            world.visualize_image(index='initial', rgb=True)

        ## for a view of the whole scene
        if (SAVE_COMPOSED_JPG or SAVE_GIF):
            world.add_camera(viz_dir, width=width//2, height=height//2, fx=fx//2, img_dir=viz_dir,
                             camera_point=(6, 4, 2), target_point=(0, 4, 1))
            world.make_transparent(world.robot.body, transparency=0)

    if USE_GYM:
        from isaac_tools.gym_utils import load_lisdf_isaacgym, record_actions_in_gym, set_camera_target_body
        frame_width = 1280
        frame_height = 800
        if LIGHT_CONF is not None:
            frame_width = 3840
            frame_height = 2160
            save_mp4 = True
            save_gif = False
        gym_world = load_lisdf_isaacgym(abspath(exp_dir), camera_width=frame_width, camera_height=frame_height,
                                        camera_point=camera_point, target_point=target_point)
        set_camera_target_body(gym_world, run_dir)

        save_name = basename(exp_dir).replace('temp_', '')

        #####################################################

        ## set better camera view for making gif screenshots
        if CAMERA_KWARGS is not None:
            camera_point = CAMERA_KWARGS['camera_point']
            camera_target = CAMERA_KWARGS['camera_target']
            gym_world.set_viewer_target(camera_point, camera_target)
            gym_world.set_camera_target(gym_world.cameras[0], camera_point, camera_target)
            if 'sink' in run_dir:
                gym_world.simulator.set_light(
                    direction=np.asarray([0, -1, 0.2]), ## [0, -1, 0.2]
                    intensity=np.asarray([1, 1, 1]))
            if True:
                img_file = gym_world.get_rgba_image(gym_world.cameras[0])
                from PIL import Image
                im = Image.fromarray(img_file)
                im.save(join('gym_images', save_name+'.png'))
                sys.exit()

        if LIGHT_CONF is not None:
            gym_world.simulator.set_light(**LIGHT_CONF)

        #####################################################

        img_dir = join(exp_dir, 'gym_images')
        gif_name = 'gym_replay.gif'
        os.mkdir(img_dir)
        gif_name = record_actions_in_gym(problem, commands, gym_world, img_dir=img_dir, body_map=body_map,
                                         gif_name=gif_name, time_step=0, verbose=False, plan=plan,
                                         save_gif=save_gif, save_mp4=save_mp4, camera_movement=CAMERA_MOVEMENT)
        # gym_world.wait_if_gui()

        new_file = join('gym_images', save_name+'.gif')
        if save_gif:
            # shutil.copy(join(exp_dir, gif_name), join(run_dir, gif_name))
            print('moved gif to {}'.format(join(run_dir, new_file)))
            shutil.move(join(exp_dir, gif_name), new_file)
        if save_mp4:
            mp4_name = gif_name.replace('.gif', '.mp4')
            new_mp4_name = new_file.replace('.gif', '.mp4')
            shutil.move(join(mp4_name), new_mp4_name)
            print('moved mp4 to {}'.format(join(run_dir, new_mp4_name)))
        del(gym_world.simulator)

    elif save_mp4:
        video_path = join(run_dir, 'replay.mp4')
        with VideoSaver(video_path):
            apply_actions(problem, commands, time_step=0.025, verbose=False, plan=plan)
        print('saved to', abspath(video_path))

    else:

        run_name = basename(run_dir)
        if not AUTO_PLAY:
            wait_unlocked()
            # wait_if_gui(f'start replay {run_name}?')
        answer = True
        if not AUTO_PLAY:
            answer = query_yes_no(f"start replay {run_name}?", default='yes')
        if answer:
            time_step = 2e-5 if SAVE_JPG else 0.02
            time_step = None if STEP_BY_STEP else time_step
            results = apply_actions(problem, commands, time_step=time_step, verbose=verbose, plan=plan,
                                    body_map=body_map, SAVE_COMPOSED_JPG=SAVE_COMPOSED_JPG, SAVE_GIF=SAVE_GIF,
                                    CHECK_COLLISIONS=CHECK_COLLISIONS, cfree_range=CFREE_RANGE,
                                    VISUALIZE_COLLISIONS=VISUALIZE_COLLISIONS)

        if CHECK_COLLISIONS:
            new_data = {'cfree': results} if results else {'cfree': CFREE_RANGE}
            ## another way to be bad data is if the place pose is not realistic
            if new_data['cfree'] == CFREE_RANGE:
                result = check_unrealistic_placement_z(world, run_dir)
                if result:
                    new_data['cfree'] = result
            add_to_planning_config(run_dir, new_data)
            if results:
                print('COLLIDED', run_dir)

        else:

            if SAVE_COMPOSED_JPG:
                episodes = results
                h, w, _ = episodes[0][0][0][0].shape
                crop = (0, h//3-h//30, w, 2*h//3-h//30)
                make_composed_image_multiple_episodes(episodes, join(world.img_dir, 'composed.jpg'),
                                                      verbose=verbose, crop=crop)

            if SAVE_GIF:
                episodes = results
                h, w, _ = episodes[0].shape
                crop = (0, h//3-h//30, w, 2*h//3-h//30)
                gif_name = 'replay.gif'
                images_to_gif(world.img_dir, gif_name, episodes, crop=crop)

            # if SAVE_COMPOSED_JPG or SAVE_GIF:
            #     world.camera = world.cameras[0]

            if SAVE_JPG:
                for sud in range(len(world.camera_kwargs)):
                    viz_dir = join(run_dir_ori, f'zoomin_{sud}'.replace('_0', ''))
                    world.add_camera(viz_dir, img_dir=viz_dir, **zoomin_kwargs, **world.camera_kwargs[sud])
                    world.visualize_image(index='final', rgb=True)
                # world.visualize_image(index='final', rgb=True, **world.camera_kwargs)

        if EVALUATE_QUALITY:
            answer = query_yes_no(f"delete this run {run_name}?", default='no')
            if answer:
                new_dir = join(MAMAO_DATA_PATH, 'impossible', f"{task_name}_{run_name}")
                shutil.move(run_dir, new_dir)
                print(f"moved {run_dir} to {new_dir}")

        # wait_if_gui('replay next run?')

    # disconnect()
    reset_simulation()
    shutil.rmtree(exp_dir)


def merge_all_wconfs(all_wconfs):
    longest_command = max([len(wconf) for wconf in all_wconfs])
    whole_wconfs = []
    for t in range(longest_command):
        whole_wconf = {}
        for num in range(len(all_wconfs)):
            if t < len(all_wconfs[num]):
                whole_wconf.update(all_wconfs[num][t])
        whole_wconfs.append(whole_wconf)
    return whole_wconfs


def replay_all_in_gym(width=1440, height=1120, num_rows=5, num_cols=5, world_size=(6, 6), verbose=False,
                      frame_gap=6, debug=False, loading_effect=False, save_gif=True, save_mp4=False,
                      camera_motion=None):
    from test_utils import get_dirs_camera
    from isaac_tools.gym_utils import load_envs_isaacgym, record_actions_in_gym, \
        update_gym_world_by_wconf, images_to_gif, images_to_mp4
    from tqdm import tqdm

    img_dir = join('gym_images')
    gif_name = 'gym_replay_batch_gym.gif'
    # if isdir(img_dir):
    #     shutil.rmtree(img_dir)
    # os.mkdir(img_dir)

    data_dir = 'test_full_kitchen_100' if loading_effect else 'test_full_kitchen_sink'
    ori_dirs, camera_point_begin, camera_point_final, target_point = get_dirs_camera(
        num_rows, num_cols, world_size, data_dir=data_dir, camera_motion=camera_motion)
    lisdf_dirs = [copy_dir_for_process(ori_dir, verbose=verbose) for ori_dir in ori_dirs]
    num_worlds = min([len(lisdf_dirs), num_rows * num_cols])

    ## translate commands into world_confs
    all_wconfs = []

    if loading_effect:
        ### load all gym_worlds and return all wconfs
        gym_world, offsets, all_wconfs = load_envs_isaacgym(lisdf_dirs, num_rows=num_rows, num_cols=num_cols,
                                                            world_size=world_size, loading_effect=True, verbose=verbose,
                                                            camera_point=camera_point_begin, target_point=target_point)
    else:
        for i in tqdm(range(num_worlds)):
            exp_dir, run_dir, commands, plan = get_pkl_run(lisdf_dirs[i], verbose=verbose)
            world = load_lisdf_pybullet(exp_dir, use_gui=not USE_GYM or debug,
                                        width=width, height=height, verbose=False)
            body_map = get_body_map(run_dir, world, larger=False)
            problem = Problem(world)
            wconfs = record_actions_in_gym(problem, commands, plan=plan, return_wconf=True,
                                           world_index=i, body_map=body_map)
            all_wconfs.append(wconfs)
            reset_simulation()
            if debug:
                wait_unlocked()

        ## load all scenes in gym
        gym_world, offsets = load_envs_isaacgym(lisdf_dirs, num_rows=num_rows, num_cols=num_cols, world_size=world_size,
                                                camera_point=camera_point_begin, target_point=target_point, verbose=verbose)

    # img_file = gym_world.get_rgba_image(gym_world.cameras[0])
    # from PIL import Image
    # im = Image.fromarray(img_file)
    # im.save(join('gym_images', 'replay_all_in_gym' + '.png'))
    # sys.exit()

    all_wconfs = merge_all_wconfs(all_wconfs)
    print(f'\n\nrendering all {len(all_wconfs)} frames')

    ## update all scenes for each frame
    filenames = []
    for i in tqdm(range(len(all_wconfs))):
        kwargs = dict(camera_point_begin=camera_point_begin,
                      camera_point_final=camera_point_final,
                      target_point=target_point)
        interpolate_camera_pose(gym_world, i, len(all_wconfs), kwargs)
        update_gym_world_by_wconf(gym_world, all_wconfs[i], offsets=offsets)

        # ## just save the initial state
        # img_file = gym_world.get_rgba_image(gym_world.cameras[0])
        # from PIL import Image
        # im = Image.fromarray(img_file)
        # im.save(join('gym_images', 'replay_all_in_gym' + '.png'))
        # sys.exit()

        if i % frame_gap == 0:
            img_file = gym_world.get_rgba_image(gym_world.cameras[0])
            filenames.append(img_file)

    save_gym_run(img_dir, gif_name, filenames, save_gif=save_gif, save_mp4=save_mp4)


def generated_recentely(file):
    result = False
    if isfile(file):
        if SKIP_IF_PROCESSED_RECENTLY:
            last_modified = os.path.getmtime(file)
            if last_modified > CHECK_TIME:
                result = True
        else:
            result = True
    return result


def case_filter(run_dir_ori):
    """ whether to process this run """
    if CASES is not None or GIVEN_PATH is not None:
        return True

    result = True
    if CHECK_COLLISIONS:
        config, mod_time = load_planning_config(run_dir_ori, return_mod_time=True)
        if 'cfree' in config: ##  and config['cfree']:
            if mod_time > 1674746024:
                return False
            if isinstance(config['cfree'], float):  ## check if unrealistic
                return True
            # if isinstance(config['cfree'], str) and exist_instance(run_dir_ori, '100015') \
            #         and 'braiser' in config['cfree']:
            #     return True
            result = False
        return result

    if SAVE_JPG:
        # run_num = eval(run_dir_ori[run_dir_ori.rfind('/')+1:])
        # if 364 <= run_num < 386:
        #     return True
        viz_dir = join(run_dir_ori, 'zoomin')
        if isdir(viz_dir):
            enough = len([a for a in listdir(viz_dir) if '.png' in a]) > 1
            file = join(viz_dir, 'rgb_image_final.png')
            result = not generated_recentely(file) or not enough
    if result:
        return result

    if SAVE_GIF:
        file = join(run_dir_ori, 'replay.gif')
        result = not generated_recentely(file)
    multiple_solutions_file = join(run_dir_ori, 'multiple_solutions.json')

    # if not result and isfile(multiple_solutions_file):
    #     plans = json.load(open(multiple_solutions_file, 'r'))
    #     if len(plans) == 2 and 'rerun_dir' in plans[0]:
    #         print('dont skip multiple solutions', run_dir_ori)
    #         result = True
    return result


if __name__ == '__main__':
    process = run_one  ## run_one | swap_microwave
    # case_filter = None
    process_all_tasks(process, args.t, parallel=args.p, cases=CASES, path=GIVEN_PATH, dir=GIVEN_DIR,
                      case_filter=case_filter)

    # replay_all_in_gym(num_rows=14, num_cols=14, world_size=(6, 6), save_gif=True)

    # record 1 : 250+ worlds
    # replay_all_in_gym(num_rows=2, num_cols=1, world_size=(4, 8), loading_effect=False,
    #                   frame_gap=1, save_mp4=True, save_gif=False, verbose=False, camera_motion='zoom')
    # replay_all_in_gym(num_rows=32, num_cols=8, world_size=(4, 8), loading_effect=True,
    #                   frame_gap=1, save_mp4=True, save_gif=False, verbose=False, camera_motion='zoom')

    # record 1 : 96+ worlds
    # replay_all_in_gym(num_rows=32, num_cols=8, world_size=(4, 8), loading_effect=False,
    #                   frame_gap=3, save_mp4=True, save_gif=False, verbose=False, camera_motion='zoom')

    ## record 2 : robot execution
    # replay_all_in_gym(num_rows=8, num_cols=3, world_size=(4, 8), loading_effect=False,
    #                   frame_gap=2, save_mp4=True, save_gif=False, verbose=False, camera_motion='splotlight')
