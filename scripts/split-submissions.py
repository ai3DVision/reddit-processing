#!/usr/bin/env python
import os, sys, csv
import logging, argparse
import hashlib, pickle
import multiprocessing as mp
import pandas as pd
import numpy as np

input_directory = ""
output_directory = ""
num_splits = 1024
pool_size = 20
target_directories = {}
compress = False
comment_post_mapping = None


def hash(s):
    return int(hashlib.md5(s.encode()).hexdigest(), 16)


def get_bucket(s):
    return hash(s) % num_splits


def listdir(directory):
    return list(map(lambda d: os.path.join(directory, d), os.listdir(directory)))


def save_dict(d, fname):
    pickle.dump(d, open(fname, 'wb'))

def load_dict(fname):
    return pickle.load(open(fname, 'rb'))

def split_by_submission(cache_fname="final_base_mapping.txt"):
    logger.debug("Creating target directories...")
    create_target_directories()
    logger.debug("Target directories created.")

    global comment_post_mapping  # stores map from comment fullname -> base submission id
    if os.path.isfile(cache_fname):
        logger.debug("Loading comment cache file: %s" % cache_fname)
        comment_post_mapping = load_dict(cache_fname)
    else:
        # The comment data must be loaded and read so that we have the mapping
        # from comment full-name to base (submission) full-name, which is required for the splitting
        # of the other data sets
        logger.info("Processing comment tables...")
        comments_directory = os.path.join(input_directory, "stanford_comment_data")
        comment_post_mapping = split_record_mapping(comments_directory, target_directories,
                                                    "post_fullname", "comment_fullname", "post_fullname")
        logger.info("Saving comment map to: %s" % cache_fname)
        save_dict(comment_post_mapping, cache_fname)

    logger.info("Processing submission tables...")
    # Must first split up the submission data because
    split_data_set("post_fullname", input_directory, "stanford_submission_data")

    #  Now split the rest of the data while adding a column using the mapping that we have
    dirs_to_split = ["stanford_report_data", "stanford_removal_data", "stanford_vote_data"]
    data_sets = [os.path.join(input_directory, directory) for directory in dirs_to_split]

    for data_set_dir in data_sets:
        mapped_split(data_set_dir, 'target_fullname', 'post_fullname')


def mapped_split(data_set_dir, mapped_col, result_column):

    args_list = [
        (data_set_dir, table_fname, mapped_col, result_column)
        for table_fname in os.listdir(data_set_dir)
    ]

    pool = mp.Pool(pool_size)
    pool.map(unpack_mapped_split_core, args_list)

def unpack_mapped_split_core(args):
    mapped_split_core(*args)


def mapped_split_core(data_set_dir, table_file_name, mapped_col, result_column):
    table_file_path = os.path.join(data_set_dir, table_file_name)

    logger.debug("Reading: %s" % table_file_name)
    df = pd.read_csv(table_file_path, engine='python')

    def get_base_submission(target_fullname):
        return comment_post_mapping[target_fullname] if target_fullname in comment_post_mapping else target_fullname

    logger.debug("Mapping column: %s" % table_file_name)
    df[result_column] = df[mapped_col].apply(get_base_submission)
    df[result_column].fillna("missing", inplace=True)

    logger.debug("Splitting: %s" % table_file_name)
    output_file_map = {i: os.path.join(target_directories[i], table_file_name) for i in target_directories}
    split_data_frame(df, result_column, get_bucket, output_file_map, compress=compress)





# For splitting comments while recodging mapping
def split_record_mapping(sub_directory, split_target_dir_mapping, on, col_mapped_from, col_mapped_to):

    def read_table(fname):
        return pd.read_csv(fname, engine="python")

    logger.debug("Reading all tables from: %s" % sub_directory)
    df = pd.concat(map(read_table, listdir(sub_directory)))  # concat all tables

    logger.debug("Mapping %s: %s -> %s" % (sub_directory, col_mapped_from, col_mapped_to))
    mapping = dict(zip(df[col_mapped_from], df[col_mapped_to]))

    def final_target_name(i):
        sub_dir_name = os.path.split(sub_directory)[1]
        return os.path.join(split_target_dir_mapping[i], sub_dir_name + ".csv")
    output_file_map = {i: final_target_name(i) for i in range(num_splits)}

    logger.debug("Splitting: %s" % sub_directory)
    split_data_frame(df, on, get_bucket, output_file_map, compress=compress)
    return mapping


# basic operations
def split_data_set(on, data_set_path, sub_dir_name):
    targets = {}
    for i in range(num_splits):
        targets[i] = os.path.join(target_directories[i], sub_dir_name)
        if not os.path.isdir(targets[i]):
            os.mkdir(targets[i])

    full_sub_data_path = os.path.join(data_set_path, sub_dir_name)
    data_files = map(lambda f: os.path.join(data_set_path, f), os.listdir(full_sub_data_path))
    args_list = [(on, table_file, targets) for table_file in data_files]
    pool = mp.Pool(pool_size)
    pool.map(unpack_split_file, args_list)

def unpack_split_file(args):
    split_file(*args)

def split_file(on, file_path, targets):
    file_name = os.path.split(file_path)[1]
    logger.debug("Reading: %s" % file_name)
    df = pd.read_csv(file_path, engine='python')
    logger.debug("Splitting: %s" % file_name)
    split_data_frame(df, on, get_bucket, targets)

def split_data_frame(df, on, assign_split, output_file_map, temp_col='bkt', compress=False):
    df[temp_col] = df[on].apply(assign_split)
    for i in output_file_map:
        df_out = df[df[temp_col] == i].drop(temp_col, axis=1)  # select rows and drop temporary column
        df_out.to_csv(output_file_map[i], index=False, compression='gzip' if compress else None)


def create_target_directories():
    global target_directories
    target_directories = {i: os.path.join(output_directory, "%05d" % i) for i in range(num_splits)}
    for i in target_directories:
        target_dir = target_directories[i]
        if os.path.isfile(target_dir):
            logger.error("File exists: %s" % target_dir)
            exit(1)
        if not os.path.isdir(target_dir):
            os.mkdir(target_dir)  # create it if it doesn't exist
    return target_directories


def parse_args():
    parser = argparse.ArgumentParser(description="Split the Reddit data-set", formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    io_options_group = parser.add_argument_group("I/O Options")
    io_options_group.add_argument('-in', "--input", help="Input directory")
    io_options_group.add_argument('-out', "--output", help="Output directory")
    io_options_group.add_argument('-c', '--compress', action='store_true', help='Compress output')
    io_options_group.add_argument('--cache', type=str, default="submission_map_cache.txt",
                                  help="Submission mapping file cache")

    options_group = parser.add_argument_group("Options")
    options_group.add_argument('-n', '--num-splits', type=int, default=1024, help="Number of ways to split data set")
    options_group.add_argument('-p', '--pool-size', type=int, default=20,    help="Thread-pool size")

    console_options_group = parser.add_argument_group("Console Options")
    console_options_group.add_argument('-v', '--verbose', action='store_true', help='verbose output')
    console_options_group.add_argument('--debug', action='store_true', help='Debug Console')
    console_options_group.add_argument('-log', '--log', nargs='?', type=str, help="Logging file",
                                       default=os.path.join("log", os.path.splitext(os.path.basename(__file__))[0] + '_log.txt'))
    return parser.parse_args()


def init_logger(args):
    global logger
    if args.debug:
        logging.basicConfig(format='[%(asctime)s][%(levelname)s][%(funcName)s] - %(message)s')
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)

    elif args.verbose:
        logging.basicConfig(format='[%(asctime)s][%(levelname)s][%(funcName)s] - %(message)s')
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)
    else:
        logging.basicConfig(format='[log][%(levelname)s] - %(message)s')
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.WARNING)

    if args.log:  # Logging file was specified
        log_file = os.path.expanduser(args.log)

        log_file_dir = os.path.split(log_file)[0]
        if not os.path.exists(log_file_dir): os.mkdir(log_file_dir)

        if os.path.isfile(log_file):
            open(args.log, 'w').close()

        log_file_handler = logging.FileHandler(log_file)
        logger.addHandler(log_file_handler)


def main():
    args = parse_args()
    init_logger(args)

    global input_directory, output_directory, num_splits, pool_size, compress
    input_directory = os.path.expanduser(args.input)
    output_directory = os.path.expanduser(args.output)
    num_splits = args.num_splits
    pool_size = args.pool_size
    compress = args.compress

    logger.debug("Input directory: %s" % input_directory)
    if os.path.isfile(input_directory)or not os.path.isdir(input_directory):
        logger.error("Not a directory: %s" % input_directory)
        raise Exception()

    if not os.path.exists(args.output):
        logger.debug("Output directory: %s did not exist. Creating it..." % output_directory)
        os.makedirs(output_directory)
    else:
        logger.debug("Output directory: %s" % output_directory)

    split_by_submission(cache_fname=args.cache)


if __name__ == "__main__":
    main()
