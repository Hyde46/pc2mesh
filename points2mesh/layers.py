from inits import *
import tensorflow as tf
from tensorpack.utils import logger
from flex_conv_layers import flex_convolution, flex_pooling, knn_bruteforce, knn_bf_sym

flags = tf.app.flags
FLAGS = flags.FLAGS

# global unique layer ID dictionary for layer name assignment
_LAYER_UIDS = {}


def get_layer_uid(layer_name=''):
    """Helper function, assigns unique layer IDs."""
    if layer_name not in _LAYER_UIDS:
        _LAYER_UIDS[layer_name] = 1
        return 1
    else:
        _LAYER_UIDS[layer_name] += 1
        return _LAYER_UIDS[layer_name]


def sparse_dropout(x, keep_prob, noise_shape):
    """Dropout for sparse tensors."""
    random_tensor = keep_prob
    random_tensor += tf.random_uniform(noise_shape)
    dropout_mask = tf.cast(tf.floor(random_tensor), dtype=tf.bool)
    pre_out = tf.sparse_retain(x, dropout_mask)
    return pre_out * (1. / keep_prob)

def dot(x, y, sparse=False):
    """Wrapper for tf.matmul (sparse vs dense)."""
    if sparse:
        res = tf.sparse_tensor_dense_matmul(x, y)
    else:
        res = tf.matmul(x, y)
    return res


class Layer(object):
    """Base layer class. Defines basic API for all layer objects.
    Implementation inspired by keras (http://keras.io).

    # Properties
        name: String, defines the variable scope of the layer.
        logging: Boolean, switches Tensorflow histogram logging on/off

    # Methods
        _call(inputs): Defines computation graph of layer
            (i.e. takes input, returns output)
        __call__(inputs): Wrapper for _call()
        _log_vars(): Log all variables
    """

    def __init__(self, **kwargs):
        allowed_kwargs = {'name', 'logging','batch_size'}
        for kwarg in kwargs.keys():
            assert kwarg in allowed_kwargs, 'Invalid keyword argument: ' + kwarg
        name = kwargs.get('name')
        #TODO:
        #self.batch_size = kwargs.get('batch_size')
        self.batch_size = 5
        if not name:
            layer = self.__class__.__name__.lower()
            name = layer + '_' + str(get_layer_uid(layer))
        self.name = name
        self.vars = {}
        logging = kwargs.get('logging', False)
        self.logging = logging
        self.sparse_inputs = False

    def _call(self, inputs):
        return inputs

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging and not self.sparse_inputs:
                tf.summary.histogram(self.name + '/inputs', inputs)
            outputs = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs', outputs)
            return outputs

    def _log_vars(self):
        for var in self.vars:
            tf.summary.histogram(self.name + '/vars/' + var, self.vars[var])


class GraphConvolution(Layer):
    """Graph convolution layer."""

    def __init__(self, input_dim, output_dim, placeholders, dropout=False,
                 sparse_inputs=False, act=tf.nn.relu, bias=True, gcn_block_id=1,
                 featureless=False, **kwargs):
        super(GraphConvolution, self).__init__(**kwargs)

        if dropout:
            self.dropout = placeholders['dropout']
        else:
            self.dropout = 0.

        self.act = act
        if gcn_block_id == 1:
            self.support = placeholders['support1']
        elif gcn_block_id == 2:
            self.support = placeholders['support2']
        elif gcn_block_id == 3:
            self.support = placeholders['support3']

        self.sparse_inputs = sparse_inputs
        self.featureless = featureless
        self.bias = bias

        # helper variable for sparse dropout
        self.num_features_nonzero = 3  # placeholders['num_features_nonzero']

        with tf.variable_scope(self.name + '_vars'):
            for i in range(len(self.support)):
                self.vars['weights_' + str(i)] = glorot([input_dim, output_dim],
                                                        name='weights_' + str(i))
            if self.bias:
                self.vars['bias'] = zeros([output_dim], name='bias')

        if self.logging:
            self._log_vars()

    def _call(self, inputs):
        B,N,Dp = inputs.shape.as_list()
        outs = []
        for b in range(B):
            x = inputs[b]

            # dropout
            if self.sparse_inputs:
                x = sparse_dropout(x, 1 - self.dropout, self.num_features_nonzero)
            else:
                x = tf.nn.dropout(x, 1 - self.dropout)

            # convolve
            supports = list()
            for i in range(len(self.support)):
                if not self.featureless:
                    pre_sup = dot(x, self.vars['weights_' + str(i)],
                                  sparse=self.sparse_inputs)
                else:
                    pre_sup = self.vars['weights_' + str(i)]
                support = dot(self.support[i], pre_sup, sparse=True)
                supports.append(support)
            output = tf.add_n(supports)

            # bias
            if self.bias:
                output += self.vars['bias']

            outs.append(self.act(output))
        #return self.act(output)
        return tf.stack(outs)


class GraphPooling(Layer):
    """Graph Pooling layer."""

    def __init__(self, placeholders,gt_pt, pool_id=1, **kwargs):
        super(GraphPooling, self).__init__(**kwargs)

        self.pool_idx = placeholders['pool_idx'][pool_id - 1]

        self.gt_pt = gt_pt
        self.K = 1

    def _call(self, inputs):
        B,N,Dp = inputs.shape.as_list() 

        outputs = [] 
        for b in range(B):
            X = inputs[b]
            add_feat = (1 / 2.0) * tf.reduce_sum(tf.gather(X, self.pool_idx), 1)
            #outputs.append( tf.concat([X, add_feat], 0) )
            out = tf.stack(outputs)
            #return out
            outputs.append(tf.concat([X, add_feat], 0))
        return tf.stack(outputs)
            #return tf.concat([X, add_feat], 0) 

class GraphAlignment(Layer):
    """ GraphAlignment
    Moves Graph features towards arithmetic middle of ground truth points
    """
    def __init__(self,gt_pt, **kwargs):
        super(GraphAlignment, self).__init__(**kwargs)
        self.gt_pt = gt_pt

    def _call(self,inputs):
        B, _, _ = inputs.shape.as_list()
        outs = []
        for b in range(B):
            mean_graph = tf.reduce_mean(inputs[b], axis=0) 
            mean_gt = tf.reduce_mean(self.gt_pt[b],axis=1)
            delta_mean = tf.subtract(mean_gt,mean_graph)
            outputs = tf.add(inputs[b], delta_mean)
            outputs = outputs / tf.reduce_max(tf.abs(outputs))
            outs.append(outputs)
        return tf.stack(outs)

class GraphProjection(Layer):
    """Graph Projection layer."""

    def __init__(self, placeholders, **kwargs):
        super(GraphProjection, self).__init__(**kwargs)
        self.K = FLAGS.num_neighbors
        self.pc_feat = placeholders['pc_feature']

        self.use_maximum = False

    def _call(self, inputs):
        stage_0 = self.mean_neighborhood(inputs,0)
        stage_1 = self.mean_neighborhood(inputs,1) 
        stage_2 = self.mean_neighborhood(inputs,2) 
        stage_3 = self.mean_neighborhood(inputs,3) 
        outputs = tf.concat([inputs, stage_0,\
                stage_1[0], stage_1[1],\
                stage_2[0], stage_2[1],\
                stage_3[0], stage_3[1]\
                ], 2)
        return outputs
        
    def mean_neighborhood(self, inputs, num_feature):
        coord = inputs
        B,N,Dp = coord.shape.as_list()

        if num_feature > 0:
            D_feature = FLAGS.feature_depth * pow(2,num_feature-1)

        #coord_expanded = tf.expand_dims( coord, -1)

        #transform PC feature to usable format
        if num_feature > 0:
            pc_coords = self.pc_feat[num_feature][0]
            pc_feature = self.pc_feat[num_feature][1]
        else:
            pc_coords = self.pc_feat[num_feature]

        ellipsoid = tf.transpose(coord,[0, 2, 1])

        Y = tf.transpose(pc_coords, [0,2,1])
        if num_feature > 0:
            Y_feature = tf.transpose(pc_feature, [0, 2, 1])

        # Neighbors: [B, K, N]
        knn,_,_ = knn_bf_sym(ellipsoid, pc_coords, K=self.K)

        # Easier shape to work on
        knnr = tf.reshape(knn, [1, B * N * self.K])

        bv = tf.ones([B,N*self.K],dtype=tf.int32) * tf.constant(np.arange(0,B),shape=[B,1],dtype=tf.int32)

        knnr = tf.stack([tf.reshape(bv,[1,B*N*self.K]), tf.reshape(knn,[1,B*N*self.K])],-1)
        knnY = tf.reshape(tf.gather_nd(Y, knnr), [B, N, self.K, Dp])
        knnY_mean = tf.reduce_mean(knnY, axis = 2)

        if num_feature > 0:
            knnY_feature = tf.reshape(tf.gather_nd(Y_feature, knnr), [B, N, self.K, D_feature])
            
            if self.use_maximum:
                max_features = tf.reduce_max(knnY_feature, axis=1, keepdims=False,name="Maximum")
                return [max_features]
                #return [knnY_mean[0], max_features]
            else:
                knnY_feature_mean = tf.reduce_mean(knnY_feature, axis = 2)
                return [knnY_mean, knnY_feature_mean]

        return knnY_mean


