import os
import gym
import random
import numpy as np
import tensorflow as tf
from collections import deque

from skimage.color import rgb2gray
from skimage.transform import resize

from tensorflow.keras.optimizers import Adam
from tensorflow.keras.layers import Conv2D, Dense, Flatten


# 상태가 입력, 큐함수가 출력인 인공신경망 생성
class DQN(tf.keras.Model):
    def __init__(self, action_size, state_size):
        super(DQN, self).__init__()
        self.conv1 = Conv2D(32, (8, 8), strides=(4, 4), activation='relu',
                            input_shape=state_size)
        self.conv2 = Conv2D(64, (4, 4), strides=(2, 2), activation='relu')
        self.conv3 = Conv2D(64, (3, 3), strides=(1, 1), activation='relu')
        self.flatten = Flatten()
        self.fc = Dense(512, activation='relu')
        self.fc_out = Dense(action_size)

    def call(self, x):
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        x = self.flatten(x)
        x = self.fc(x)
        q = self.fc_out(x)
        return q


# 브레이크아웃 예제에서의 DQN 에이전트
class DQNAgent:
    def __init__(self, action_size, state_size=(84, 84, 4)):
        self.render = False

        # 상태와 행동의 크기 정의
        self.state_size = state_size
        self.action_size = action_size

        # DQN 하이퍼파라미터
        self.discount_factor = 0.99
        self.learning_rate = 1e-4
        self.epsilon = 1.
        self.epsilon_start, self.epsilon_end = 1.0, 0.02
        self.exploration_steps = 1000000.
        self.epsilon_decay_step = self.epsilon_start - self.epsilon_end
        self.epsilon_decay_step /= self.exploration_steps
        self.batch_size = 32
        self.train_start = 50000
        self.update_target_rate = 10000

        # 리플레이 메모리, 최대 크기 100,000
        self.memory = deque(maxlen=100000)
        # 게임 시작 후 랜덤하게 움직이지 않는 것에 대한 옵션
        self.no_op_steps = 30

        # 모델과 타깃 모델 생성
        self.model = DQN(action_size, state_size)
        self.target_model = DQN(action_size, state_size)
        self.optimizer = Adam(self.learning_rate, clipnorm=10.)
        # 타깃 모델 초기화
        self.update_target_model()

        self.avg_q_max, self.avg_loss = 0, 0

        self.writer = tf.summary.create_file_writer('summary/breakout_dqn')
        self.model_path = os.path.join(os.getcwd(), 'save_model', 'model')

    # 타깃 모델을 모델의 가중치로 업데이트
    def update_target_model(self):
        self.target_model.set_weights(self.model.get_weights())

    # 입실론 탐욕 정책으로 행동 선택
    def get_action(self, history):
        history = np.float32(history / 255.0)
        if np.random.rand() <= self.epsilon:
            return random.randrange(self.action_size)
        else:
            q_value = self.model(history)
            return np.argmax(q_value[0])

    # 샘플 <s, a, r, s'>을 리플레이 메모리에 저장
    def append_sample(self, history, action, reward, next_history, dead):
        self.memory.append((history, action, reward, next_history, dead))

    # 텐서보드에 학습 정보를 기록
    def draw_tensorboard(self, score, step, episode):
        with self.writer.as_default():
            tf.summary.scalar('Total Reward/Episode', score, step=episode)
            tf.summary.scalar('Average Max Q/Episode',
                              self.avg_q_max / float(step), step=episode)
            tf.summary.scalar('Duration/Episode', step, step=episode)
            tf.summary.scalar('Average Loss/Episode',
                              self.avg_loss / float(step), step=episode)

    # 리플레이 메모리에서 무작위로 추출한 배치로 모델 학습
    def train_model(self):
        if self.epsilon > self.epsilon_end:
            self.epsilon -= self.epsilon_decay_step

        # 메모리에서 배치 크기만큼 무작위로 샘플 추출
        batch = random.sample(self.memory, self.batch_size)

        history = np.array([sample[0][0] / 255. for sample in batch],
                           dtype=np.float32)
        actions = np.array([sample[1] for sample in batch])
        rewards = np.array([sample[2] for sample in batch])
        next_history = np.array([sample[3][0] / 255. for sample in batch],
                                dtype=np.float32)
        dones = np.array([sample[4] for sample in batch])

        # 학습 파라메터
        model_params = self.model.trainable_variables
        with tf.GradientTape() as tape:
            # 현재 상태에 대한 모델의 큐함수
            predicts = self.model(history)
            one_hot_action = tf.one_hot(actions, self.action_size)
            predicts = tf.reduce_sum(one_hot_action * predicts, axis=1)

            # 다음 상태에 대한 타깃 모델의 큐함수
            target_predicts = self.target_model(next_history)

            # 벨만 최적 방정식을 구성하기 위한 타깃과 큐함수의 최대 값 계산
            max_q = np.amax(target_predicts, axis=1)
            targets = rewards + (1 - dones) * self.discount_factor * max_q

            # 후버로스 계산
            error = tf.abs(targets - predicts)
            quadratic_part = tf.clip_by_value(error, 0.0, 1.0)
            linear_part = error - quadratic_part
            loss = tf.reduce_mean(0.5 * tf.square(quadratic_part) + linear_part)

            self.avg_loss += loss.numpy()

        # 오류함수를 줄이는 방향으로 모델 업데이트
        grads = tape.gradient(loss, model_params)
        self.optimizer.apply_gradients(zip(grads, model_params))


# 학습속도를 높이기 위해 흑백화면으로 전처리
# 입력 이미지를 84x84로 바꾸고 RGB를 Gray로 변경한다.
def pre_processing(observe):
    processed_observe = np.uint8(
        resize(rgb2gray(observe), (84, 84), mode='constant') * 255)
    return processed_observe


if __name__ == "__main__":
    # 1. 환경을 불러온다.
    env = gym.make('BreakoutDeterministic-v4')
    print('env.frameskip:{}',env.frameskip)
    # 2. agent를 생성한다.
    agent = DQNAgent(action_size=3)

    # 3.1. 변수를 선언하고 초기화 한다.
    global_step = 0
    score_avg = 0
    score_max = 0
    
    # 3.2. 최대로 학습할 에피소드 값을 설정한다.
    num_episode = 50000
    
    # 3.3. 불필요한 행동을 없애주기 위한 딕셔너리 선언
    action_dict = {0:1, 1:2, 2:3, 3:3}

    # 4. 정해준 에피소드만큼 학습을 진행한다.
    for e in range(num_episode):
        done = False
        dead = False
        
        # 4.1. 스텝, 스코어값을 초기화하고, 목숨 5개가 주어진다. 
        step, score, start_life = 0, 0, 5
        # 4.2. 환경의 초기값을 가져온다.
        observe = env.reset()

        # 4.3. 랜덤으로 뽑힌 값 만큼의 프레임동안 움직이지 않음
        for _ in range(random.randint(1, agent.no_op_steps)):
            observe, _, _, _ = env.step(1)

        # 4.4. 환경의 초기값을 이미지 전처리 한다.
        state = pre_processing(observe)
        
        # 4.5. 전처리된 이미지 4장을 히스토리로 만든다. 
        #      초기화이므로 동일한 이미지로 히스토리를 만든다.
        history = np.stack((state, state, state, state), axis=2)
        history = np.reshape([history], (1, 84, 84, 4))

        # 4.6. 게임이 끝날 때까지 계속 실행하는 while문
        while not done:
            
            # 4.6.1. render의 유무를 확인한다.
            #        render의 유무에 따라 학습 속도가 달라진다.
            if agent.render:
                env.render()
            
            # 4.6.2. 글로벌 스텝, 스텝을 하나 증가한다.
            global_step += 1
            step += 1

            # 4.6.3. 바로 전 history를 입력으로 받아 행동을 선택
            action = agent.get_action(history)
            # 1: 정지, 2: 왼쪽, 3: 오른쪽
            real_action = action_dict[action]

            # 4.6.4. 만약 죽었다면, 다시 시작하기 위해 발사 행동을 함
            if dead:
                action, real_action, dead = 0, 1, False

            # 4.6.5. 선택한 행동으로 환경에서 한 타임스텝 진행
            observe, reward, done, info = env.step(real_action)

            # 4.6.6. 각 타임스텝마다 상태 전처리
            #        기존 3장의 히스토리에 새로운 이미지를 추가
            next_state = pre_processing(observe)
            next_state = np.reshape([next_state], (1, 84, 84, 1))
            next_history = np.append(next_state, history[:, :, :, :3], axis=3)

            # 4.6.7. avg_q_max를 계산하기 위해 현재 모델 Q값의 max를 agent.avg_q_max에 더한다.
            agent.avg_q_max += np.amax(agent.model(np.float32(history / 255.))[0])

            # 4.6.8. 만약 dead인 경우 dead를 True로 변경하고, start_life를 하나 줄여준다. 
            if start_life > info['ale.lives']:
                dead = True
                start_life = info['ale.lives']

            # 리워드를 스코어에 더해준다.
            score += reward
            # 리워드의 크기를 -1~1사이로 범위를 한정한다.
            reward = np.clip(reward, -1., 1.)
            # 샘플 <s, a, r, s'>을 리플레이 메모리에 저장 후 학습
            agent.append_sample(history, action, reward, next_history, dead)

            # 리플레이 메모리 크기가 정해놓은 수치에 도달한 시점부터 모델 학습 시작
            # train_start가 현재 50000으로 설정되어 있고, 50,000스텝이후부터 학습이 시작된다.
            if len(agent.memory) >= agent.train_start:
                agent.train_model()
                # 일정 시간마다 타겟모델을 모델의 가중치로 업데이트
                if global_step % agent.update_target_rate == 0:
                    agent.update_target_model()

            # 만약에 죽으면 4장의 프레임을 모두 동일한 값으로 초기화 한다.
            # 죽지 않았으면 next_history를 그대로 history로 받는다.
            if dead:
                history = np.stack((next_state, next_state,
                                    next_state, next_state), axis=2)
                history = np.reshape([history], (1, 84, 84, 4))
            else:
                history = next_history

            # 게임이 끝났으면 에피소드의 학습정보를 기록하여 출력한다.
            if done:
                # 각 에피소드 당 학습 정보를 기록
                if global_step > agent.train_start:
                    agent.draw_tensorboard(score, step, e)

                score_avg = 0.9 * score_avg + 0.1 * score if score_avg != 0 else score
                score_max = score if score > score_max else score_max

                log = "episode: {:5d} | ".format(e)
                log += "score: {:4.1f} | ".format(score)
                log += "score max : {:4.1f} | ".format(score_max)
                log += "score avg: {:4.1f} | ".format(score_avg)
                log += "memory length: {:5d} | ".format(len(agent.memory))
                log += "epsilon: {:.3f} | ".format(agent.epsilon)
                log += "q avg : {:3.2f} | ".format(agent.avg_q_max / float(step))
                log += "avg loss : {:3.2f}".format(agent.avg_loss / float(step))
                print(log)

                agent.avg_q_max, agent.avg_loss = 0, 0

        # 500 에피소드마다 모델 저장
        if e % 500 == 0:
            agent.model.save_weights("./save_model/model", save_format="tf")
