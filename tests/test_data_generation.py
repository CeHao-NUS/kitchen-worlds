#!/usr/bin/env python

from __future__ import print_function
import shutil
import pickle
import os
import time
import random
import json
from os.path import join, abspath, dirname, isdir, isfile, basename
from config import EXP_PATH, OUTPUT_PATH

from pddlstream.language.constants import Equal, AND, print_solution, PDDLProblem
from pddlstream.algorithms.meta import solve, create_parser

from pybullet_tools.utils import disconnect, LockRenderer, has_gui, WorldSaver, wait_if_gui, \
    SEPARATOR, get_aabb, wait_for_duration, has_gui, reset_simulation, set_random_seed, \
    set_numpy_seed, set_renderer
from pybullet_tools.bullet_utils import summarize_facts, print_goal, nice, get_datetime
from pybullet_tools.pr2_agent import solve_multiple, post_process, pddlstream_from_state_goal, \
    create_cwd_saver
from pybullet_tools.pr2_primitives import control_commands, State, apply_commands
from pybullet_tools.logging import parallel_print, myprint

from lisdf_tools.lisdf_loader import pddl_files_from_dir

from world_builder.actions import apply_actions
from world_builder.world_generator import save_to_outputs_folder

from test_utils import parallel_processing
from test_world_builder import create_pybullet_world

NUM_PROBLEMS = 1
DEFAULT_TEST = 'test_fridges_tables' ## 'test_one_fridge' | 'test_fridge_table' | 'test_fridges_tables'
PARALLEL = False
DIVERSE = False
USE_GUI = False
SEED = None


def get_args(test_name=DEFAULT_TEST, output_name='one_fridge_pick_pr2', n_problems=NUM_PROBLEMS,
             parallel=PARALLEL, diverse=DIVERSE, use_gui=USE_GUI, seed=SEED):

    parser = create_parser()
    parser.add_argument('-test', type=str, default=test_name,
                        help='Name of a sub-dir in test_cases/ generated by cognitive-architectures')
    parser.add_argument('-output_dir', type=str, default=output_name,
                        help='Name of the output folder inside outputs/')
    parser.add_argument('-n_problems', type=int, default=n_problems,
                        help='Number of sampled problems (scene & goal)')
    parser.add_argument('-p', '--parallel', action='store_true', default=parallel)
    parser.add_argument('-d', '--diverse', action='store_true', default=diverse)

    parser.add_argument('-v', '--viewer', action='store_true', default=use_gui)
    parser.add_argument('-t', '--time_step', type=float, default=4e-0)
    parser.add_argument('-s', '--seed', type=int, default=seed, help='')
    parser.add_argument('-cam', '--camera', action='store_true', default=True, help='')
    parser.add_argument('-seg', '--segment', action='store_true', default=False, help='')
    parser.add_argument('-cfree', action='store_true', help='Disables collisions during planning')
    parser.add_argument('-enable', action='store_true', help='Enables rendering during planning')
    parser.add_argument('-teleport', action='store_true', help='Teleports between configurations')
    parser.add_argument('-simulate', action='store_true', help='Simulates the system')
    # parser.add_argument('-simulate', action='store_true', help='Simulates the system')

    args = parser.parse_args()
    seed = args.seed
    if seed is None:
        seed = random.randint(0, 10 ** 6 - 1)
    set_random_seed(seed)
    set_numpy_seed(seed)
    args.seed = seed
    print('Seed:', seed)
    return args


args = get_args()


def init_data_run(test_name, data_name):
    """ inside each data folder, to be generated:
        - before planning:
            [x] scene.lisdf
            [x] problem.pddl
            [x] planning_config.json
            [x] log.txt (generated before planning)
        - after planning:
            [x] plan.json
            [x] commands.pkl
            [x] log.json (generated by pddlstream)
        - before training (optional):
            [ ] crop_images
            [ ] diverse_plans.json
            [ ] features.txt
    """
    testcase_dir = join(EXP_PATH, test_name)
    output_dir = join(OUTPUT_PATH, data_name)
    if isdir(testcase_dir):
        os.mkdir(output_dir)
        for f in ['domain.pddl', 'domain_full.pddl', 'stream.pddl']:
            shutil.copy(join(testcase_dir, f), join(output_dir, f))


def get_builder(test_name):
    from world_builder.builders import test_one_fridge as test_scene
    if test_name == 'test_one_fridge':
        from world_builder.builders import test_one_fridge as test_scene
    elif test_name == 'test_fridge_table':
        from world_builder.builders import test_fridge_table as test_scene
    elif test_name == 'test_fridges_tables':
        from world_builder.builders import test_fridges_tables as test_scene
    return test_scene

#####################################


def process(exp_dir):
    """ exist a version in cognitive-architectures for generating mini-datasets (single process),
        run in kitchen-worlds for parallelization, but no reliable planning time data """

    """ STEP 0 -- COPY PDDL FILES """
    # init_data_run(args.test, output_dir)
    print(exp_dir)

    """ STEP 1 -- GENERATE SCENES """
    state, goal, file = create_pybullet_world(
        args, get_builder(args.test), world_name=basename(exp_dir), SAMPLING=True,
        template_name=args.test, out_dir=exp_dir, DEPTH_IMAGES=False,
        SAVE_LISDF=False, SAVE_TESTCASE=True, root_dir=EXP_PATH)
    world = state.world
    saver = WorldSaver()

    domain_path, stream_path, config_path = pddl_files_from_dir(exp_dir, replace_pddl=False)
    cwd_saver = create_cwd_saver()
    print_fn = parallel_print ## if args.parallel else myprint
    print_fn(args)

    pddlstream_problem = pddlstream_from_state_goal(
        state, goal, domain_pddl=domain_path, stream_pddl=stream_path,
        custom_limits=world.robot.custom_limits, collisions=not args.cfree,
        teleport=args.teleport, print_fn=print_fn)
    stream_info = world.robot.get_stream_info(partial=False, defer=False)

    kwargs = {'visualize': True}
    if args.diverse:
        kwargs.update(dict(
                diverse=True,
                downward_time=20,  ## max time to get 100, 10 sec, 30 sec for 300
                evaluation_time=60,  ## on each skeleton
                max_plans=200,  ## number of skeletons
        ))
    start = time.time()
    solution, tmp_dir = solve_multiple(pddlstream_problem, stream_info, lock=not args.enable,
                                       cwd_saver=cwd_saver, **kwargs)

    print_solution(solution)
    plan, cost, evaluations = solution

    """ =============== log plan and planning time =============== """
    t = None if args.parallel else round(time.time() - start, 3)
    if plan is None:
        plan_log = None
        plan_len = None
        init = None
    else:
        plan_log = [str(a) for a in plan]
        plan_len = len(plan)
        init = [[str(a) for a in f] for f in evaluations.preimage_facts]
    time_log = [{
        'planning': t, 'plan': plan_log, 'plan_len': plan_len, 'init': init
    }, {'total_planning': t}]
    with open(join(exp_dir, f'plan.json'), 'w') as f:
        json.dump(time_log, f, indent=4)

    """ =============== save planing log =============== """
    txt_file = join(tmp_dir, 'txt_file.txt')
    if isfile(txt_file):
        shutil.move(txt_file, join(exp_dir, f"log.txt"))
    txt_file = join(tmp_dir, 'visualizations', 'log.json')
    if isfile(txt_file):
        shutil.move(txt_file, join(exp_dir, f"log.json"))
    cwd_saver.restore()

    """ =============== save commands for replay =============== """
    with LockRenderer(lock=not args.enable):
        commands = post_process(state, plan)
        state.remove_gripper()
        saver.restore()
    with open(join(exp_dir, f"commands.pkl"), 'wb') as f:
        pickle.dump(commands, f)

    """ =============== visualize the plan =============== """
    if (plan is None) or not has_gui():
        end_process(exp_dir, args.output_dir)
        return

    print(SEPARATOR)
    saver.restore()
    # wait_if_gui('Execute?')
    if args.simulate:  ## real physics
        control_commands(commands)
    else:
        set_renderer(True)
        apply_actions(state, commands, time_step=1e-2, verbose=False)
    # wait_if_gui('Finish?')
    end_process(exp_dir, args.output_dir)


def end_process(exp_dir, output_dir):
    print(SEPARATOR)
    shutil.move(exp_dir, join(OUTPUT_PATH, output_dir, basename(exp_dir)))
    reset_simulation()
    disconnect()


def collect_for_fastamp():
    start = get_datetime()
    exp_dirs = [join(EXP_PATH, f'{args.output_dir}_{start}_{i}') for i in range(args.n_problems)]
    parallel_processing(process, exp_dirs, parallel=args.parallel)


if __name__ == '__main__':
    collect_for_fastamp()
