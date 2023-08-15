import tensorflow as tf
from tensorflow.python.ops import tensor_array_ops, control_flow_ops


class TARGET_LSTM(object):
    def __init__(self, opt, FLAGS, params, pretrain = True):
        self.num_emb = opt.vocab_size
        self.emb_dim = opt.g_emb_dim
        self.hidden_dim = opt.g_hid_dim
        self.seq_len = opt.seq_len
        self.g_params = []
        if pretrain:
            self.batch_size = FLAGS.gen_pre_batch_size
        else:
            self.batch_size = FLAGS.gen_batch_size
        self.start_token = tf.constant([opt.start_token] * self.batch_size, dtype=tf.int32)
        self.params = params

        tf.set_random_seed(66)

        with tf.variable_scope('generator'):
            self.g_embeddings = tf.Variable(self.params[0])
            self.g_params.append(self.g_embeddings)
            self.g_recurrent_unit = self.create_recurrent_unit(self.g_params)  # maps h_tm1 to h_t for generator
            self.g_output_unit = self.create_output_unit(self.g_params)  # maps h_t to o_t (output token logits)

        # placeholder definition
        self.x = tf.placeholder(tf.int32, shape=[self.batch_size, self.seq_len]) # sequence of tokens generated by generator

        # processed for batch
        with tf.device("/cpu:0"):
            self.processed_x = tf.transpose(tf.nn.embedding_lookup(self.g_embeddings, self.x), perm=[1, 0, 2])  # seq_length x batch_size x emb_dim

        # initial states
        self.h0 = tf.zeros([self.batch_size, self.hidden_dim])
        self.h0 = tf.stack([self.h0, self.h0])

        # generator on initial randomness
        gen_o = tensor_array_ops.TensorArray(dtype=tf.float32, size=self.seq_len,
                                             dynamic_size=False, infer_shape=True)
        gen_x = tensor_array_ops.TensorArray(dtype=tf.int32, size=self.seq_len,
                                             dynamic_size=False, infer_shape=True)

        def _g_recurrence(i, x_t, h_tm1, gen_o, gen_x):
            h_t = self.g_recurrent_unit(x_t, h_tm1)  # hidden_memory_tuple
            o_t = self.g_output_unit(h_t)  # batch x vocab , logits not prob
            log_prob = tf.log(tf.nn.softmax(o_t))
            next_token = tf.cast(tf.reshape(tf.multinomial(log_prob, 1), [self.batch_size]), tf.int32)
            x_tp1 = tf.nn.embedding_lookup(self.g_embeddings, next_token)  # batch x emb_dim
            gen_o = gen_o.write(i, tf.reduce_sum(tf.multiply(tf.one_hot(next_token, self.num_emb, 1.0, 0.0),
                                                             tf.nn.softmax(o_t)), 1))  # [batch_size] , prob
            gen_x = gen_x.write(i, next_token)  # indices, batch_size
            return i + 1, x_tp1, h_t, gen_o, gen_x

        _, _, _, self.gen_o, self.gen_x = control_flow_ops.while_loop(
            cond=lambda i, _1, _2, _3, _4: i < self.seq_len,
            body=_g_recurrence,
            loop_vars=(tf.constant(0, dtype=tf.int32),
                       tf.nn.embedding_lookup(self.g_embeddings, self.start_token), self.h0, gen_o, gen_x)
            )

        self.gen_x = self.gen_x.stack()  # seq_length x batch_size
        self.gen_x = tf.transpose(self.gen_x, perm=[1, 0])  # batch_size x seq_length

        # supervised pretraining for generator
        g_predictions = tensor_array_ops.TensorArray(
            dtype=tf.float32, size=self.seq_len,
            dynamic_size=False, infer_shape=True)

        ta_emb_x = tensor_array_ops.TensorArray(
            dtype=tf.float32, size=self.seq_len)
        ta_emb_x = ta_emb_x.unstack(self.processed_x)

        def _pretrain_recurrence(i, x_t, h_tm1, g_predictions):
            h_t = self.g_recurrent_unit(x_t, h_tm1)
            o_t = self.g_output_unit(h_t)
            g_predictions = g_predictions.write(i, tf.nn.softmax(o_t))  # batch x vocab_size
            x_tp1 = ta_emb_x.read(i)
            return i + 1, x_tp1, h_t, g_predictions

        _, _, _, self.g_predictions = control_flow_ops.while_loop(
            cond=lambda i, _1, _2, _3: i < self.seq_len,
            body=_pretrain_recurrence,
            loop_vars=(tf.constant(0, dtype=tf.int32),
                       tf.nn.embedding_lookup(self.g_embeddings, self.start_token),
                       self.h0, g_predictions))

        self.g_predictions = tf.transpose(
            self.g_predictions.stack(), perm=[1, 0, 2])  # batch_size x seq_length x vocab_size

        # pretraining loss
        self.pretrain_loss = -tf.reduce_sum(
            tf.one_hot(tf.to_int32(tf.reshape(self.x, [-1])), self.num_emb, 1.0, 0.0) * tf.log(
                tf.reshape(self.g_predictions, [-1, self.num_emb]))) / (self.seq_len * self.batch_size)

        self.out_loss = tf.reduce_sum(
            tf.reshape(
                -tf.reduce_sum(
                    tf.one_hot(tf.to_int32(tf.reshape(self.x, [-1])), self.num_emb, 1.0, 0.0) * tf.log(
                        tf.reshape(self.g_predictions, [-1, self.num_emb])), 1
                ), [-1, self.seq_len]
            ), 1
        )  # batch_size

    def generate(self, session):
        return session.run(self.gen_x)

    def init_matrix(self, shape):
        return tf.random_normal(shape, stddev=1.0)

    def create_recurrent_unit(self, params):
        # Weights and Bias for input and hidden tensor
        self.Wi = tf.Variable(self.params[1])
        self.Ui = tf.Variable(self.params[2])
        self.bi = tf.Variable(self.params[3])

        self.Wf = tf.Variable(self.params[4])
        self.Uf = tf.Variable(self.params[5])
        self.bf = tf.Variable(self.params[6])

        self.Wog = tf.Variable(self.params[7])
        self.Uog = tf.Variable(self.params[8])
        self.bog = tf.Variable(self.params[9])

        self.Wc = tf.Variable(self.params[10])
        self.Uc = tf.Variable(self.params[11])
        self.bc = tf.Variable(self.params[12])
        params.extend([
            self.Wi, self.Ui, self.bi,
            self.Wf, self.Uf, self.bf,
            self.Wog, self.Uog, self.bog,
            self.Wc, self.Uc, self.bc])

        def unit(x, hidden_memory_tm1):
            previous_hidden_state, c_prev = tf.unstack(hidden_memory_tm1)

            # Input Gate
            i = tf.sigmoid(
                tf.matmul(x, self.Wi) +
                tf.matmul(previous_hidden_state, self.Ui) + self.bi
            )

            # Forget Gate
            f = tf.sigmoid(
                tf.matmul(x, self.Wf) +
                tf.matmul(previous_hidden_state, self.Uf) + self.bf
            )

            # Output Gate
            o = tf.sigmoid(
                tf.matmul(x, self.Wog) +
                tf.matmul(previous_hidden_state, self.Uog) + self.bog
            )

            # New Memory Cell
            c_ = tf.nn.tanh(
                tf.matmul(x, self.Wc) +
                tf.matmul(previous_hidden_state, self.Uc) + self.bc
            )

            # Final Memory cell
            c = f * c_prev + i * c_

            # Current Hidden state
            current_hidden_state = o * tf.nn.tanh(c)

            return tf.stack([current_hidden_state, c])

        return unit

    def create_output_unit(self, params):
        self.Wo = tf.Variable(self.params[13])
        self.bo = tf.Variable(self.params[14])
        params.extend([self.Wo, self.bo])

        def unit(hidden_memory_tuple):
            hidden_state, c_prev = tf.unstack(hidden_memory_tuple)
            # hidden_state : batch x hidden_dim
            logits = tf.matmul(hidden_state, self.Wo) + self.bo
            # output = tf.nn.softmax(logits)
            return logits

        return unit
