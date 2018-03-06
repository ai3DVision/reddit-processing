#!/usr/bin/env python
import os, sys, csv
import logging, argparse
import hashlib
import multiprocessing as mp
import pandas as pd
import pickle

input_directory = ""
output_directory = ""
num_splits = 1024
pool_size = 20
target_directories = {}
compress = False


def hash(s):
    return int(hashlib.md5(s.encode()).hexdigest(), 16)


def get_bucket(s):
    return hash(s) % num_splits


def listdir(directory):
    return list(map(lambda d: os.path.join(directory, d), os.listdir(directory)))


def split_by_submission():
    logger.debug("Creating target directories...")
    create_target_directories()
    logger.debug("Target directories created.")

    logger.info("Processing submission tables...")
    # Must first split up the submission data because
    submissions_directory = os.path.join(input_directory, "stanford_submission_data")
    submission_base_mapping = split_record_mapping(submissions_directory, target_directories,
                                                   "post_fullname", "post_fullname", "post_fullname")

    # The comment data must be loaded and read so that we have the mapping
    # from comment full-name to base (submission) full-name, which is required for the splitting
    # of the other data sets
    logger.info("Processing comment tables...")
    comments_directory = os.path.join(input_directory, "stanford_comment_data")
    comment_base_mapping = split_record_mapping(comments_directory, target_directories,
                                                "post_fullname", "comment_fullname", "post_fullname")

    final_base_mapping = {**comment_base_mapping, **submission_base_mapping}
    with open("final_base_mapping.txt", 'w') as f:
        f.write(pickle.dumps(final_base_mapping))

    # Now split the rest of the data while adding a column that
    dirs_to_split = ["stanford_report_data", "stanford_removal_data", "stanford_vote_data"]
    data_sets = [os.path.join(input_directory, directory) for directory in dirs_to_split]

    for data_set_dir in data_sets:
        mapped_split(data_set_dir, 'target_fullname', 'post_fullname', final_base_mapping)


def mapped_split(data_set_dir, mapped_col, result_column, value_mapping):

    args_list = [
        (data_set_dir, table_fname, mapped_col, result_column, value_mapping)
        for table_fname in os.listdir(data_set_dir)
    ]

    pool = mp.Pool(pool_size)
    pool.map(unpack_mapped_split_core, args_list)


def unpack_mapped_split_core(args):
    mapped_split_core(*args)

def mapped_split_core(data_set_dir, table_file_name, mapped_col, result_column, value_mapping):
    table_file_path = os.path.join(data_set_dir, table_file_name)

    logger.debug("Reading: %s" % table_file_name)
    df = pd.read_csv(table_file_path, engine='python')

    logger.debug("Mapping column: %s" % table_file_name)
    df[result_column] = df[mapped_col].map(value_mapping)  # create a new column that is a mapping of the other one

    logger.debug("Splitting: %s" % table_file_name)
    split_data_frame(df, result_column, get_bucket, target_directories, compress=compress)


def split_record_mapping(sub_directory, split_target_dir_mapping, on, col_mapped_from, col_mapped_to):

    args_list = [
        (sub_directory, table_fname, split_target_dir_mapping, on, col_mapped_from, col_mapped_to)
        for table_fname in os.listdir(sub_directory)
    ]

    pool = mp.Pool(pool_size)
    maps = pool.map(unkack_core, args_list)

    mapping = {}
    for d in maps:
        mapping.update(d)
    return mapping


def unkack_core(args):
    return core(*args)

def core(sub_directory, table_file_name, split_target_dir_mapping, on, col_mapped_from, col_mapped_to):
    mapping = {}
    table_file_path = os.path.join(sub_directory, table_file_name)

    logger.debug("Reading: %s" % table_file_name)
    df = pd.read_csv(table_file_path, engine='python')

    logger.debug("Mapping %s -> %s: %s" % (col_mapped_from, col_mapped_to, table_file_name))
    mapping.update(dict(zip(df[col_mapped_from], df[col_mapped_to])))

    logger.debug("Splitting: %s" % table_file_name)
    output_file_map = {i: os.path.join(split_target_dir_mapping[i], table_file_name) for i in range(num_splits)}
    split_data_frame(df, on, get_bucket, output_file_map, compress=compress)

    return mapping


def split_table_file(on, table_file_path, split_target_dir_mapping):
    """
    Splits a table stored in a file on a specified column, writing it to the
    :param on: The column of the table to split on
    :param table_file_path: Full path to the file containing the table to split
    :param split_target_dir_mapping: A mapping from bucket/split number to output directory to write results
    :return: None
    """
    table_file_name = os.path.split(table_file_path)[1]
    logger.debug("Reading: %s" % table_file_name)
    df = pd.read_csv(table_file_path, engine='python')
    logger.debug("Splitting: %s" % table_file_name)
    split_data_frame(df, on, split_target_dir_mapping, compress=compress)


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

    options_group = parser.add_argument_group("Options")
    options_group.add_argument('-n', '--num-splits', type=int, default=1024, help="Number of ways to split data set")
    options_group.add_argument('-p', '--pool-size', type=int, default=20, help="Thread-pool size")
    options_group.add_argument('-on', '--on', type=str, default="user_id", help="Field to split on")

    console_options_group = parser.add_argument_group("Console Options")
    console_options_group.add_argument('-v', '--verbose', action='store_true', help='verbose output')
    console_options_group.add_argument('--debug', action='store_true', help='Debug Console')
    console_options_group.add_argument('-log', '--log', type=str, default=None, help="Logging file")

    args = parser.parse_args()

    global logger
    if args.debug:
        logging.basicConfig(filename=args.log, format='[%(asctime)s][%(levelname)s][%(funcName)s] - %(message)s')
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)

    elif args.verbose:
        logging.basicConfig(filename=args.log, format='[%(asctime)s][%(levelname)s][%(funcName)s] - %(message)s')
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)
    else:
        logging.basicConfig(filename=args.log, format='[log][%(levelname)s] - %(message)s')
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.WARNING)

    return args


def main():
    args = parse_args()

    global input_directory, output_directory, num_splits, pool_size, compress
    input_directory = args.input
    output_directory = args.output
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

    split_by_submission()


if __name__ == "__main__":
    main()
