#coding=utf-8
import tensorflow as tf
import os
from model.ner_model import ner_model
from model.data import gen_data,load_vocs,gen_voc,save_dev,parse_ner_content
from model.f1 import compare_f1 
from ner_config import nerConfig
from tqdm import tqdm

'''
cpu性能参数设置
'''
config = tf.compat.v1.ConfigProto()
config.intra_op_parallelism_threads = 8
config.inter_op_parallelism_threads = 1
os.environ['KMP_BLOCKTIME'] = "1"
os.environ['KMP_SETTINGS'] = "1"
os.environ['KMP_AFFINITY'] = "granularity=fine,verbose,compat,1,0"
os.environ['OMP_NUM_THREADS'] = "8"
tf.compat.v1.Session(config=config)

train_data_ts = "./train_data/ts/train.json"
dev_data_ts = "./train_data/ts/dev.json"
train_data_cand = "./train_data/ts/train.json"
dev_data_cand = "./train_data/ts/dev.json"

data_voc = "./voc_dir/data.vocab"
label_voc = "./voc_dir/label.vocab"
checkpoint_dir = "checkpoint/"

def build_train_op(config):
    ner = ner_model(config,training=True)
    train_loss = tf.keras.metrics.Mean(name='train_loss')
    optimizer=tf.keras.optimizers.Adam(config.lr)
    #设置检查点
    ckpt = tf.train.Checkpoint(ner=ner,optimizer=optimizer)
    ckpt_manager = tf.train.CheckpointManager(ckpt, checkpoint_dir + 'trains', max_to_keep=2)
    #恢复旧模型
    ckpt.restore(ckpt_manager.latest_checkpoint)
    if ckpt_manager.latest_checkpoint:
        print("Restored from {}".format(ckpt_manager.latest_checkpoint))
    else:
        print("creat new model....")
    return ner, train_loss, ckpt, ckpt_manager, optimizer

def train(train_set,train_ops):
    ner, train_loss, ckpt, ckpt_manager, optimizer = train_ops
    train_step_signature = [tf.TensorSpec(shape=(None, None), dtype=tf.int32),
                            tf.TensorSpec(shape=(None, None), dtype=tf.int32)]
    @tf.function(input_signature=train_step_signature)
    def train_step(input_ids,input_labels):
        with tf.GradientTape() as tape:
            loss = ner(input_ids,input_labels)
        gradients = tape.gradient(loss, ner.trainable_variables)
        optimizer.apply_gradients(zip(gradients, ner.trainable_variables))
        train_loss(loss)

    train_loss.reset_states()
    tq = tqdm(enumerate(train_set))
    for index,batch in tq:
        batch_data,batch_label = batch
        train_step(batch_data,batch_label)
        tq.set_description('Epoch {} Loss {:.4f}'.format(epoch,train_loss.result()))
        if index % 50 == 0 and index > 0:
            save_path = ckpt_manager.save()
            print("Saved checkpoint {}".format(save_path))

def infer(config,dev_set,data_vocab,labels_vocab):
    ner = ner_model(config,training=False)
    #从训练的检查点恢复权重
    ckpt = tf.train.Checkpoint(ner=ner)
    latest_ckpt = tf.train.latest_checkpoint(checkpoint_dir + 'trains')
    #添加expect_partial()关闭优化器相关节点warnning打印
    status = ckpt.restore(latest_ckpt).expect_partial()
    #定义infer函数
    infer_step_signature = [tf.TensorSpec(shape=(None, None), dtype=tf.int32)]
    @tf.function(input_signature=infer_step_signature)
    def predict(input_ids):
        L = ner(input_ids)
        return L

    infers = []
    datas = []
    for batch_val in dev_set:
        batch_val_data,_ = batch_val
        infers.append(predict(batch_val_data))
        datas.append(batch_val_data)
    infer_json = parse_ner_content(infers,datas,[data_vocab,labels_vocab],config.label_train_type)
    with open("test/infer.json",'w') as wf:
        wf.write('\n'.join(infer_json))
    _,average_f1 = compare_f1('test/infer.json','test/dev.json')
    tf.saved_model.save(ner, checkpoint_dir + 'infers/')
    print('average f1 is {}'.format(average_f1))

if __name__ == '__main__':
    config = nerConfig()
    #选择数据集
    train_file = train_data_cand
    dev_file = dev_data_cand
    if config.train_type == "ts":
        train_file = train_data_ts
        dev_file = dev_data_ts
    #检查字典是否存在或者重新生成字典标志位设置
    if not os.path.isfile(data_voc) or config.reset_vocab:
        gen_voc([train_file,dev_file],save_data_file=data_voc,save_label_file=label_voc)
        save_dev(dev_file,'test/dev.json')
    #载入字典文件
    data_vocab,labels_vocab = load_vocs(data_voc,label_voc)
    #载入训练数据和测试数据
    train_set = gen_data(dataset=train_file,
        vocabs=[data_vocab,labels_vocab],
        batch_size=config.batch_size,
        is_training=True,
        label_train_type=config.label_train_type)
    dev_set = gen_data(dataset = dev_file,
        vocabs=[data_vocab,labels_vocab],
        batch_size=config.batch_size,
        is_training=False,
        label_train_type=config.label_train_type)
    #训练和测试
    training = True 
    if training:
        train_ops = build_train_op(config)
        #开始训练数据
        for epoch in range(config.epoch):
            train(train_set,train_ops)
            infer(config,dev_set,data_vocab,labels_vocab)
    else:
        infer(config,dev_set,data_vocab,labels_vocab)
