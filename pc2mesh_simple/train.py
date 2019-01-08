import tensorflow as tf
import numpy as np
import cv2
import argparse
import os
from tensorpack import *
from tensorpack.input_source import QueueInput

from PointCloudDataFlow import get_modelnet_dataflow
from models import *
from fetcher import *


enable_argscope_for_module(tf.layers)

#TOTAL_BATCH_SIZE = 16
TOTAL_BATCH_SIZE = 1
BATCH_SIZE = 1
NUM_EPOCH = 10

PC = {'num': 1024, 'dp':3, 'ver':"40"}

seed = 1024
np.random.seed(seed)
tf.set_random_seed(seed)

# Settings
flags = tf.app.flags
FLAGS = flags.FLAGS
flags.DEFINE_integer('coord_dim', 3, 'Number of units in output layer')
flags.DEFINE_integer('feat_dim', 963, 'Number of units in perceptual featuer layer.')
flags.DEFINE_integer('hidden', 192, 'Number of units in hidden layer')
flags.DEFINE_float('weight_decay', 5e-6, 'Weight decay for L2 loss.')
flags.DEFINE_float('learning_rate', 3e-5, 'Initial learning rage.')
flags.DEFINE_integer('pc_num', 1024, 'Number of points per pointcloud object')
flags.DEFINE_integer('dp', 3, 'Dimension of points in pointcloud')
flags.DEFINE_integer('num_neighbors', 8, 'Number of neighbors considered during Graph projection layer')


# Define placholders(dict) and model
# maybe replace later on
# Batches!
num_supports = 2
num_blocks = 3
placeholders = {
    'features': tf.placeholder(tf.float32, shape=(None, 3)),  # initial 3D coordinates of ellipsoids
#    'positions': tf.placeholder(tf.float32, shape=(None, PC['dp'], PC['num'])), # Ground truth positions and input as well
#    'vertex_normals': tf.placeholder(tf.float32, shape=(None,PC['dp'], PC['num'] )),  # ground truth (point cloud with vertex normal)
    'support1': [tf.sparse_placeholder(tf.float32) for _ in range(num_supports)],  # graph structure in the first block
    'support2': [tf.sparse_placeholder(tf.float32) for _ in range(num_supports)],  # graph structure in the second block
    'support3': [tf.sparse_placeholder(tf.float32) for _ in range(num_supports)],  # graph structure in the third block
    'faces': [tf.placeholder(tf.int32, shape=(None, 4)) for _ in range(num_blocks)],  # helper for face loss (not used)
    'edges': [tf.placeholder(tf.int32, shape=(None, 2)) for _ in range(num_blocks)],  # helper for normal loss
    'lape_idx': [tf.placeholder(tf.int32, shape=(None, 10)) for _ in range(num_blocks)],
    # helper for laplacian regularization
    'pool_idx': [tf.placeholder(tf.int32, shape=(None, 2)) for _ in range(num_blocks - 1)]  # helper for graph unpooling
        }


def construct_feed_dict(pkl, placeholders):
    coord = pkl[0]
    pool_idx = pkl[4]
    faces = pkl[5]
    lape_idx = pkl[7]
    edges = []
    for i in range(1, 4):
        adj = pkl[i][1]
        edges.append(adj[0])

    feed_dict = dict()
    feed_dict.update({placeholders['features']: coord})
    feed_dict.update({placeholders['edges'][i]: edges[i] for i in range(len(edges))})
    feed_dict.update({placeholders['faces'][i]: faces[i] for i in range(len(faces))})
    feed_dict.update({placeholders['pool_idx'][i]: pool_idx[i] for i in range(len(pool_idx))})
    feed_dict.update({placeholders['lape_idx'][i]: lape_idx[i] for i in range(len(lape_idx))})
    feed_dict.update({placeholders['support1'][i]: pkl[1][i] for i in range(len(pkl[1]))})
    feed_dict.update({placeholders['support2'][i]: pkl[2][i] for i in range(len(pkl[2]))})
    feed_dict.update({placeholders['support3'][i]: pkl[3][i] for i in range(len(pkl[3]))})
    return feed_dict

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--gpu', help='comma separated list of GPU(s) to use.')
    parser.add_argument('--load', help='load model')
    parser.add_argument('--fusion', help='run sampling', default='')
    args = parser.parse_args()

    if args.gpu:
        os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu

    logger.set_logger_dir('train_log/fusion_%s' % (args.fusion))

    #Loading Data
    df_train = get_modelnet_dataflow('train', batch_size=BATCH_SIZE,
            num_points=PC["num"], model_ver=PC["ver"], shuffle=False, normals=True)
    df_test = get_modelnet_dataflow('test', batch_size= 2 * BATCH_SIZE,
            num_points=PC["num"], model_ver = PC["ver"], shuffle=False, normals=True)
    steps_per_epoch = len(df_train)

    pkl = pickle.load(open('utils/ellipsoid/info_ellipsoid.dat', 'rb'))
    feed_dict = construct_feed_dict(pkl, placeholders)

    #Setup Model
    #Setup training step
    config = TrainConfig(
            model = pc2MeshModel(placeholders),
            #TODO:
            #data = QueueInput(df_train),
            data = FeedInput(df_train),
            callbacks=[
                ModelSaver(),
                #InferenceRunner()
                ],
            extra_callbacks=[
                #ProgressBar(['accuracy','cross_entropy_loss']),
                ],
            steps_per_epoch=steps_per_epoch,
            max_epoch=NUM_EPOCH
            )
    launch_train_with_config(config, SimpleTrainer())

