import numpy as np
import tensorflow as tf
import gym, roboschool
import logz
import os
import time
import inspect
from multiprocessing import Process

import utils as U


##################################################
# Utilities
##################################################
def build_mlp(
        input_placeholder,
        output_size,
        scope,
        n_layers=2,
        size=64,
        activation=tf.tanh,
        output_activation=None  # None means linear activation
        ):
    ##################################################
    # ----------SECTION 3----------
    # Network building
    #
    # Your code should make a feedforward neural network (also called a
    # multilayer perceptron) with 'n_layers' hidden layers of size 'size'
    # units.
    #
    # The output layer should have size 'output_size' and activation
    # 'output_activation'.
    #
    # Hint: use tf.layers.dense
    ##################################################

    with tf.variable_scope(scope):
        inputs = input_placeholder
        for _ in range(n_layers):
            inputs = tf.layers.dense(
                inputs=inputs,
                units=size,
                activation=activation)
        output_layer = tf.layers.dense(
            inputs=inputs,
            units=output_size,
            activation=output_activation)
    return output_layer


def pathlength(path):
    return len(path["reward"])


##################################################
# Policy Gradient
##################################################
def train_PG(exp_name='',
             env_name='CartPole-v0',
             n_iter=100,
             gamma=1.0,
             min_timesteps_per_batch=1000,
             max_path_length=None,
             learning_rate=5e-3,
             reward_to_go=True,
             animate=True,
             logdir=None,
             normalize_advantages=True,
             nn_baseline=False,
             seed=0,
             # network arguments
             n_layers=1,
             size=32
             ):

    start = time.time()

    # Configure output directory for logging
    logz.configure_output_dir(logdir)

    # Log experimental parameters
    args = inspect.getargspec(train_PG)[0]
    locals_ = locals()
    params = {k: locals_[k] if k in locals_ else None for k in args}
    logz.save_params(params)

    # Set random seeds
    tf.set_random_seed(seed)
    np.random.seed(seed)

    # Make the gym environment
    env = gym.make(env_name)

    # Is this env continuous, or discrete?
    discrete = isinstance(env.action_space, gym.spaces.Discrete)

    # Maximum length for episodes
    max_path_length = max_path_length or env.spec.max_episode_steps

    ##################################################
    # Notes on notation:
    #
    # sy_: symbolic variables, to distinguish them from the numerical values
    # that are computed later in the function
    #
    # Prefixes and suffixes:
    # ob: observation
    # ac: action
    # _no: observations (X); shape: (batch size /n/, observation dim)
    # _na: actions (y); shape: (batch size /n/, action dim)
    # _n: this tensor should have shape (batch size /n/)
    #
    # Note: batch size /n/ is defined at runtime, and until then, the shape for
    # that axis is None
    ##################################################

    # Observation and action sizes
    ob_dim = env.observation_space.shape[0]
    ac_dim = env.action_space.n if discrete else env.action_space.shape[0]

    ##################################################
    #                           ----------SECTION 4----------
    # Placeholders
    #
    # Need these for batch observations / actions / advantages in policy
    # gradient loss function.
    ##################################################

    # input to the policy network (X)
    sy_ob_no = tf.placeholder(
        shape=[None, ob_dim], name="ob", dtype=tf.float32)

    if discrete:
        sy_ac_na = tf.placeholder(
            shape=[None], name="ac", dtype=tf.int32)
    else:
        sy_ac_na = tf.placeholder(
            shape=[None, ac_dim], name="ac", dtype=tf.float32)

    # Define a placeholder for advantages
    sy_adv_n = tf.placeholder(
        shape=[None], name='adv', dtype=tf.float32)

    ##################################################
    # ----------SECTION 4----------
    # Networks
    #
    # Make symbolic operations for
    #   1. Policy network outputs which describe the policy distribution.
    #       a. For the discrete case, just logits for each action.
    #
    #       b. For the continuous case, the mean / log std of a Gaussian
    #          distribution over actions.
    #
    #      Hint: use the 'build_mlp' function you defined in utilities.
    #
    #      Note: these ops should be functions of the placeholder 'sy_ob_no'
    #
    #   2. Producing samples stochastically from the policy distribution.
    #
    #       a. For the discrete case, an op that takes in logits and produces
    #       actions.
    #
    #          Should have shape [None]
    #
    #       b. For the continuous case, use the reparameterization trick:
    #
    #          The output from a Gaussian distribution with mean 'mu' and std
    #          'sigma' is
    #
    #               mu + sigma * z,         z ~ N(0, I)
    #
    #          This reduces the problem to just sampling z. (Hint: use
    #          tf.random_normal!)
    #
    #          Should have shape [None, ac_dim]
    #
    #      Note: these ops should be functions of the policy network output
    #      ops.
    #
    #   3. Computing the log probability of a set of actions that were actually
    #      taken, according to the policy.
    #
    #      Note: these ops should be functions of the placeholder 'sy_ac_na',
    #      and the policy network output ops.
    #
    ##################################################

    sy_output_layer = build_mlp(
        input_placeholder=sy_ob_no,
        output_size=ac_dim,
        scope='policy_nn',
        n_layers=n_layers,
        size=size,
    )

    if discrete:
        sy_logits_na = sy_output_layer
        # Based on the multinomial distribution defined by logits, sample one
        # action for each observation
        # [:, 0]: to be compatible with later usage of sy_sampled_ac
        sy_sampled_ac = tf.multinomial(sy_logits_na, 1)[:, 0]
        sy_logprob_n = tf.nn.sparse_softmax_cross_entropy_with_logits(
            labels=sy_ac_na, logits=sy_logits_na
        )
    else:
        sy_mean = sy_output_layer
        # logstd should just be a trainable variable, not a network output.
        sy_logstd = tf.Variable(tf.zeros([1, ac_dim]))
        sy_std = tf.exp(sy_logstd)

        sy_sampled_ac = tf.random_normal(
            # note off-diagonal elements are 0, meaning no correlation among
            # different dimensions in the gaussian
            shape=tf.shape(sy_mean), mean=sy_mean, stddev=sy_std)

        # Hint: Use the log probability under a multivariate gaussian.
        mvn = tf.contrib.distributions.MultivariateNormalDiag(
            sy_mean, sy_std)
        sy_logprob_n = tf.log(mvn.prob(sy_mean))

        # code equivalent the implementation at https://github.com/EbTech/CS294/blob/58766d6d22d997c9c97e860b38ab95faf376162c/hw2/train_pg.py#L196
        # sy_mean = build_mlp(sy_ob_no, ac_dim, "policy", n_layers=n_layers, size=size)
        # sy_logstd = tf.Variable(tf.zeros([1, ac_dim]), name="policy/logstd", dtype=tf.float32)
        # sy_std = tf.exp(sy_logstd)

        # # Sample an action from the stochastic policy
        # sy_sampled_z = tf.random_normal(tf.shape(sy_mean))
        # sy_sampled_ac = sy_mean + sy_std * sy_sampled_z

        # # Likelihood of chosen action
        # sy_z = (sy_ac_na - sy_mean) / sy_std
        # sy_logprob_n = -0.5 * tf.reduce_sum(tf.square(sy_z), axis=1)


    ##################################################
    # ----------SECTION 4----------
    # Loss Function and Training Operation
    ##################################################

    # construct a pseudo-loss such that the gradient of its corresponding
    # computation graph is the policy gradient, within tf.reduce_mean is the
    # weighted negative likelihoods
    loss = tf.reduce_mean(tf.multiply(sy_logprob_n, sy_adv_n))
    update_op = tf.train.AdamOptimizer(learning_rate).minimize(loss)

    ##################################################
    #                           ----------SECTION 5----------
    # Optional Baseline
    ##################################################

    if nn_baseline:
        baseline_prediction = tf.squeeze(build_mlp(
                                sy_ob_no,
                                1,
                                "nn_baseline",
                                n_layers=n_layers,
                                size=size))
        # Define placeholders for targets, a loss function and an update op for fitting a 
        # neural network baseline. These will be used to fit the neural network baseline. 
        # YOUR_CODE_HERE
        baseline_update_op = TODO

    ##################################################
    # Tensorflow Engineering: Config, Session, Variable initialization
    ##################################################

    tf_config = tf.ConfigProto(
        inter_op_parallelism_threads=1,
        intra_op_parallelism_threads=1
    )

    sess = tf.Session(config=tf_config)
    sess.__enter__()                        # equivalent to `with sess:`
    tf.global_variables_initializer().run()  # pylint: disable=E1101

    ##################################################
    # Training Loop
    ##################################################

    total_timesteps = 0
    for itr in range(n_iter):
        print("********** Iteration %i ************" % itr)

        # Collect paths until we have enough timesteps
        timesteps_this_batch = 0
        paths = []
        while True:
            ob = env.reset()
            obs, acs, rewards = [], [], []
            animate_this_episode = (
                len(paths) == 0 and (itr % 10 == 0) and animate)
            steps = 0
            while True:
                if animate_this_episode:
                    env.render()
                    time.sleep(0.05)
                obs.append(ob)
                # ob[None] is equivalent to ob.reshape(1, -1) in this case,
                # i.e. turning ob into a sequence of observations with a length
                # of 1 so that can be fed to the nn
                ac = sess.run(sy_sampled_ac, feed_dict={sy_ob_no: ob[None]})
                ac = ac[0]
                acs.append(ac)
                ob, rew, done, _ = env.step(ac)
                rewards.append(rew)
                steps += 1
                if done or steps > max_path_length:
                    break
            path = {
                "observation": np.array(obs),
                "reward": np.array(rewards),
                "action": np.array(acs)
            }
            paths.append(path)
            timesteps_this_batch += pathlength(path)
            if timesteps_this_batch > min_timesteps_per_batch:
                break
        total_timesteps += timesteps_this_batch

        # Build arrays for observation, action for the policy gradient update
        # by concatenating across paths
        ob_no = np.concatenate([path["observation"] for path in paths])
        ac_na = np.concatenate([path["action"] for path in paths])

        ##################################################
        # ----------SECTION 4----------
        # Computing Q-values
        #
        # Your code should construct numpy arrays for Q-values which will be
        # used to compute advantages (which will in turn be fed to the
        # placeholder you defined above).
        #
        # Recall that the expression for the policy gradient PG is
        #
        #       PG = E_{tau} [sum_{t=0}^T grad log pi(a_t|s_t) * (Q_t - b_t )]
        #
        # where
        #
        #       tau=(s_0, a_0, ...) is a trajectory,
        #       Q_t is the Q-value at time t, Q^{pi}(s_t, a_t),
        #       and b_t is a baseline which may depend on s_t.
        #
        # You will write code for two cases, controlled by the flag
        # 'reward_to_go':
        #
        #   Case 1: trajectory-based PG
        #
        #       (reward_to_go = False)
        #
        #       Instead of Q^{pi}(s_t, a_t), we use the total discounted reward
        #       summed over entire trajectory (regardless of which time step
        #       the Q-value should be for).
        #
        #       For this case, the policy gradient estimator is
        #
        #           E_{tau} [sum_{t=0}^T grad log pi(a_t|s_t) * Ret(tau)]
        #
        #       where
        #
        #           Ret(tau) = sum_{t'=0}^T gamma^t' r_{t'}.
        #
        #       Thus, you should compute
        #
        #           Q_t = Ret(tau)
        #
        #   Case 2: reward-to-go PG 
        #
        #       (reward_to_go = True)
        #
        #       Here, you estimate Q^{pi}(s_t, a_t) by the discounted sum of
        #       rewards starting from time step t. Thus, you should compute
        #
        #           Q_t = sum_{t'=t}^T gamma^(t'-t) * r_{t'}
        #
        #
        # Store the Q-values for all timesteps and all trajectories in a
        # variable 'q_n', like the 'ob_no' and 'ac_na' above.
        #
        ##################################################

        # YOUR_CODE_HERE
        q_n = []
        if reward_to_go == False:
            for path in paths:
                q_t = []
                for k in range(len(path['reward'])):
                    T = k + 1
                    ret = []
                    for t in range(T):
                        _r = 0
                        for t_prime in range(T):
                            _r += gamma ** t_prime * path['reward'][t_prime]
                        ret.append(_r)
                    q_t.append(np.sum(ret))

                q_n.append(q_t)
                # vectorized version
                # path_len = path['reward'].shape[0]
                # gammas = np.repeat(gamma, path_len)
                # powers = np.arange(path_len)
                # discounts = gammas ** powers
                # discounted_rewards = discounts * path['reward']
                # discounted_rewards_sum = np.cumsum(discounted_rewards)
        else:
            for path in paths:
                q_t = []
                for k in range(len(path['reward'])):
                    T = k + 1
                    ret = []
                    for t in range(T):
                        _r = 0
                        for t_prime in range(t, T):
                            _r += gamma ** (t_prime - t) * path['reward'][t_prime]
                        ret.append(_r)
                    q_t.append(np.sum(ret))
                q_n.append(q_t)
        q_n = np.concatenate(q_n)

        ##################################################
        #                           ----------SECTION 5----------
        # Computing Baselines
        ##################################################

        if nn_baseline:
            # If nn_baseline is True, use your neural network to predict
            # reward-to-go at each timestep for each trajectory, and save the
            # result in a variable 'b_n' like 'ob_no', 'ac_na', and 'q_n'.
            #
            # Hint #bl1: rescale the output from the nn_baseline to match the
            # statistics (mean and std) of the current or previous batch of
            # Q-values. (Goes with Hint #bl2 below.)

            b_n = TODO
            adv_n = q_n - b_n
        else:
            adv_n = q_n.copy()

        ##################################################
        # ----------SECTION 4----------
        # Advantage Normalization
        ##################################################

        if normalize_advantages:
            # On the next line, implement a trick which is known empirically to
            # reduce variance in policy gradient methods: normalize adv_n to
            # have mean zero and std=1. YOUR_CODE_HERE
            adv_n = (adv_n - np.mean(adv_n)) / (np.std(adv_n + 1e-8))


        ##################################################
        #                           ----------SECTION 5----------
        # Optimizing Neural Network Baseline
        ##################################################
        if nn_baseline:
            # ----------SECTION 5----------
            # If a neural network baseline is used, set up the targets and the inputs for the 
            # baseline. 
            # 
            # Fit it to the current batch in order to use for the next iteration. Use the 
            # baseline_update_op you defined earlier.
            #
            # Hint #bl2: Instead of trying to target raw Q-values directly, rescale the 
            # targets to have mean zero and std=1. (Goes with Hint #bl1 above.)

            # YOUR_CODE_HERE
            pass

        ##################################################
        # ----------SECTION 4----------
        # Performing the Policy Update
        ##################################################

        # Call the update operation necessary to perform the policy gradient
        # update based on the current batch of rollouts.
        #
        # For debug purposes, you may wish to save the value of the loss
        # function before and after an update, and then log them below.

        # YOUR_CODE_HERE
        feed_dict = {
            sy_ob_no: ob_no,
            sy_ac_na: ac_na,
            sy_adv_n: q_n
        }
        logz.log_tabular("loss before update", loss.eval(feed_dict=feed_dict))

        # multiple updates per sampling is WRONG because the trajectories are
        # sampled from the specific one policy before a single update. After
        # one update, the trajectories do not correspond to the new policy any
        # more.

        # for i in range(100):
        #     sess.run(update_op, feed_dict=feed_dict)
        logz.log_tabular("loss after update", loss.eval(feed_dict=feed_dict))

        # Log diagnostics
        returns = [path["reward"].sum() for path in paths]
        ep_lengths = [pathlength(path) for path in paths]
        logz.log_tabular("Time", time.time() - start)
        logz.log_tabular("Iteration", itr)
        logz.log_tabular("AverageReturn", np.mean(returns))
        logz.log_tabular("StdReturn", np.std(returns))
        logz.log_tabular("MaxReturn", np.max(returns))
        logz.log_tabular("MinReturn", np.min(returns))
        logz.log_tabular("EpLenMean", np.mean(ep_lengths))
        logz.log_tabular("EpLenStd", np.std(ep_lengths))
        logz.log_tabular("TimestepsThisBatch", timesteps_this_batch)
        logz.log_tabular("TimestepsSoFar", total_timesteps)
        logz.dump_tabular()
        logz.pickle_tf_vars()


def main():
    args = U.get_args()
    logdir = U.setup_logdir(args.exp_name, args.env_name)

    max_path_length = args.ep_len if args.ep_len > 0 else None

    for e in range(args.n_experiments):
        seed = args.seed + 10 * e
        print('Running experiment with seed %d'% seed)

        def train_func():
            train_PG(
                exp_name=args.exp_name,
                env_name=args.env_name,
                n_iter=args.n_iter,
                gamma=args.discount,
                min_timesteps_per_batch=args.batch_size,
                max_path_length=max_path_length,
                learning_rate=args.learning_rate,
                reward_to_go=args.reward_to_go,
                animate=args.render,
                logdir=os.path.join(logdir, '%d' % seed),
                normalize_advantages=not(args.dont_normalize_advantages),
                nn_baseline=args.nn_baseline,
                seed=seed,
                n_layers=args.n_layers,
                size=args.size
                )

        # Awkward hacky process runs, because Tensorflow does not like
        # repeatedly calling train_PG in the same thread.
        p = Process(target=train_func, args=tuple())
        p.start()
        p.join()


if __name__ == "__main__":
    main()
