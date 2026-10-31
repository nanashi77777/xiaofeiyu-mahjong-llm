"""Convert majsoul_wrapper MIT weights to OpenCV ONNX (conversion needs onnx).

NPZ input is an export of the original state_dict, using torch.load with
weights_only=True in an isolated conversion environment. Runtime needs no torch.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def convert(source, target):
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    weights = np.load(source, allow_pickle=False)
    nodes, tensors = [], [numpy_helper.from_array(weights[name], name) for name in weights.files]
    current = 'image'
    for layer in ('conv1','conv2'):
        nodes.extend([
            helper.make_node('Conv',[current,layer+'.weight',layer+'.bias'],[layer],kernel_shape=[5,5]),
            helper.make_node('Relu',[layer],[layer+'_relu']),
            helper.make_node('MaxPool',[layer+'_relu'],[layer+'_pool'],kernel_shape=[2,2],strides=[2,2]),
        ])
        current = layer+'_pool'
    nodes.append(helper.make_node('Flatten',[current],['flat'],axis=1))
    current = 'flat'
    for layer in ('fc1','fc2','fc3'):
        nodes.append(helper.make_node('Gemm',[current,layer+'.weight',layer+'.bias'],[layer],transB=1))
        current = layer
        if layer != 'fc3':
            current = layer+'_relu'
            nodes.append(helper.make_node('Relu',[layer],[current]))
    graph = helper.make_graph(nodes,'majsoul_wrapper_TileNet',
                              [helper.make_tensor_value_info('image',TensorProto.FLOAT,['batch',3,32,32])],
                              [helper.make_tensor_value_info('fc3',TensorProto.FLOAT,['batch',38])],tensors)
    model = helper.make_model(graph,producer_name='xiaofeiyu-mahjong-llm',opset_imports=[helper.make_opsetid('',13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    target.parent.mkdir(parents=True,exist_ok=True)
    onnx.save(model,target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('target',type=Path)
    args = parser.parse_args()
    convert(args.source,args.target)
