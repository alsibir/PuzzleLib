import numpy as np

from PuzzleLib.Backend import gpuarray
from PuzzleLib.Backend import Blas
from PuzzleLib.Backend.Dnn.InstanceNorm import instanceNorm2d, instanceNorm2dBackward

from PuzzleLib.Variable import Variable
from PuzzleLib.Modules.Module import ModuleError, Module


class InstanceNorm2D(Module):
	def __init__(self, numOfMaps, epsilon=1e-5, affine=True, name=None):
		super().__init__(name)
		self.registerBlueprint(locals())

		self.numOfMaps = numOfMaps
		self.epsilon = epsilon

		self.affine = affine

		shape = (1, numOfMaps, 1, 1)
		scale = np.ones(shape, dtype=np.float32)

		self.scale = None
		self.bias = None

		self.setVar("scale", Variable(gpuarray.to_gpu(scale)))
		self.setVar("bias", Variable(gpuarray.zeros(shape, dtype=np.float32)))

		self.savemean, self.saveinvvar, self.extscale, self.scalegrad, self.biasgrad = None, None, None, None, None


	def updateData(self, data):
		self.data, self.savemean, self.saveinvvar, self.extscale = instanceNorm2d(data, self.scale,
																				  self.bias, self.epsilon)


	def updateGrad(self, grad):
		if self.affine:
			self.grad, self.scalegrad, self.biasgrad = instanceNorm2dBackward(grad, self.inData, self.extscale,
																			  self.savemean, self.saveinvvar,
																			  self.epsilon, True)
		else:
			self.grad = instanceNorm2dBackward(grad, self.inData, self.extscale, self.savemean, self.saveinvvar,
											   self.epsilon, False)


	def accGradParams(self, grad, scale=1.0, momentum=0.0):
		if self.affine:
			Blas.addVectorToVector(self.scalegrad.ravel(), self.vars["scale"].grad.ravel(),
								   out=self.vars["scale"].grad.ravel(), alpha=scale, beta=momentum)
			Blas.addVectorToVector(self.biasgrad.ravel(), self.vars["bias"].grad.ravel(),
								   out=self.vars["bias"].grad.ravel(), alpha=scale, beta=momentum)


	def checkDataShape(self, shape):
		if len(shape) != 4:
			raise ModuleError("Data must be 4d tensor")


	def checkGradShape(self, shape):
		if shape != self.data.shape:
			raise ModuleError("Inconsistency in grad shape - expected %s (%s given)" % (self.data.shape, shape))


	def dataShapeFrom(self, shape):
		return shape


	def gradShapeFrom(self, shape):
		return shape


	def reset(self):
		super().reset()
		self.savemean, self.saveinvvar, self.extscale = None, None, None

		if self.affine:
			self.scalegrad, self.biasgrad = None, None


def unittest():
	batchsize, maps, h, w = 5, 3, 4, 4
	data = gpuarray.to_gpu(np.random.randn(batchsize, maps, h, w).astype(np.float32))

	instNorm2d = InstanceNorm2D(maps, affine=True)
	instNorm2d(data)

	hostData = data.get().reshape(data.shape[0] * data.shape[1], -1)
	hostVar = np.var(hostData, axis=1)
	hostInvVar = np.ones(hostData.shape[0], dtype=np.float32) / np.sqrt(hostVar + instNorm2d.epsilon)
	hostOutData = (hostData - np.mean(hostData, axis=1, keepdims=True)) * hostInvVar[:, np.newaxis]

	assert np.allclose(instNorm2d.data.get(), hostOutData.reshape(data.shape))

	from PuzzleLib import Config
	if Config.backend == Config.Backend.intel:
		assert np.allclose(instNorm2d.saveinvvar.get().ravel(), hostVar)
	else:
		assert np.allclose(instNorm2d.saveinvvar.get().ravel(), hostInvVar)

	grad = gpuarray.to_gpu(np.random.randn(batchsize, maps, h, w).astype(np.float32))
	instNorm2d.backward(grad)

	hostGrad = grad.get().reshape(grad.shape[0] * grad.shape[1], -1)
	hostCorrs = np.empty(shape=hostInvVar.shape, dtype=np.float32)
	for i in range(hostCorrs.shape[0]):
		hostCorrs[i] = np.dot(hostGrad[i], hostOutData[i]) / hostGrad.shape[1]
	hostInGrad = hostGrad - np.mean(hostGrad, axis=1, keepdims=True) - \
				 hostCorrs[:, np.newaxis] * instNorm2d.data.get().reshape(hostOutData.shape)
	hostInGrad *= hostInvVar[:, np.newaxis]

	assert np.allclose(hostInGrad.reshape(grad.shape), instNorm2d.grad.get())


if __name__ == "__main__":
	unittest()
