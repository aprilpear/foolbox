from __future__ import absolute_import

import numpy as np
import logging

from .base import DifferentiableModel


class KerasModel(DifferentiableModel):
    """Creates a :class:`Model` instance from a `Keras` model.

    Parameters
    ----------
    model : `keras.models.Model`
        The `Keras` model that should be attacked.
    bounds : tuple
        Tuple of lower and upper bound for the pixel values, usually
        (0, 1) or (0, 255).
    channel_axis : int
        The index of the axis that represents color channels.
    preprocessing: 2-element tuple with floats or numpy arrays
        Elementwises preprocessing of input; we first subtract the first
        element of preprocessing from the input and then divide the input by
        the second element.
    predicts : str
        Specifies whether the `Keras` model predicts logits or probabilities.
        Logits are preferred, but probabilities are the default.

    """

    def __init__(
            self,
            model,
            bounds,
            channel_axis=3,
            preprocessing=(0, 1),
            predicts='probabilities'):

        super(KerasModel, self).__init__(bounds=bounds,
                                         channel_axis=channel_axis,
                                         preprocessing=preprocessing)

        from keras import backend as K
        import keras
        from pkg_resources import parse_version

        assert parse_version(keras.__version__) >= parse_version('2.0.7'), 'Keras version needs to be 2.0.7 or newer'  # noqa: E501

        if predicts == 'probs':
            predicts = 'probabilities'
        assert predicts in ['probabilities', 'logits']

        images_input = model.input
        labels_input = K.placeholder(shape=(None,))

        predictions = model.output

        shape = K.int_shape(predictions)
        _, num_classes = shape
        assert num_classes is not None

        self._num_classes = num_classes

        if predicts == 'probabilities':
            if K.backend() == 'tensorflow':
                predictions, = predictions.op.inputs
                loss = K.sparse_categorical_crossentropy(
                    labels_input, predictions, from_logits=True)
            else:  # pragma: no cover
                logging.warning('relying on numerically unstable conversion'
                                ' from probabilities to softmax')
                loss = K.sparse_categorical_crossentropy(
                    labels_input, predictions, from_logits=False)

                # transform the probability predictions into logits, so that
                # the rest of this code can assume predictions to be logits
                predictions = self._to_logits(predictions)
        elif predicts == 'logits':
            loss = K.sparse_categorical_crossentropy(
                labels_input, predictions, from_logits=True)

        loss = K.sum(loss, axis=0)
        grads = K.gradients(loss, [images_input])

        grad_loss_output = K.placeholder(shape=predictions.shape)
        external_loss = K.batch_dot(predictions, grad_loss_output, axes=-1)
        external_loss = K.sum(external_loss, axis=0)
        grads_loss_input = K.gradients(external_loss, [images_input])

        assert isinstance(grads, list)
        grad, = grads
        assert isinstance(grads_loss_input, list)
        grad_loss_input, = grads_loss_input

        self._loss_fn = K.function(
            [images_input, labels_input],
            [loss])
        self._batch_pred_fn = K.function(
            [images_input], [predictions])
        self._pred_grad_fn = K.function(
            [images_input, labels_input],
            [predictions, grad])
        self._batch_grad_fn = K.function(
            [images_input, labels_input],
            [grad])
        self._bw_grad_fn = K.function(
            [grad_loss_output, images_input],
            [grad_loss_input])

    def _to_logits(self, predictions):  # pragma: no cover
        from keras import backend as K
        eps = 10e-8
        predictions = K.clip(predictions, eps, 1 - eps)
        predictions = K.log(predictions)
        return predictions

    def num_classes(self):
        return self._num_classes

    def batch_predictions(self, images):
        px, _ = self._process_input(images)
        predictions, = self._batch_pred_fn([px])
        assert predictions.shape == (images.shape[0], self.num_classes())
        return predictions

    def predictions_and_gradient(self, image, label):
        input_shape = image.shape
        px, dpdx = self._process_input(image)
        predictions, gradient = self._pred_grad_fn([
            px[np.newaxis],
            np.array([label])])
        predictions = np.squeeze(predictions, axis=0)
        gradient = np.squeeze(gradient, axis=0)
        gradient = self._process_gradient(dpdx, gradient)
        assert predictions.shape == (self.num_classes(),)
        assert gradient.shape == input_shape
        return predictions, gradient

    def batch_gradients(self, images, labels):
        px, dpdx = self._process_input(images)
        g, = self._batch_grad_fn([px, labels])
        g = self._process_gradient(dpdx, g)
        assert g.shape == images.shape
        return g

    def batch_backward(self, gradients, images):
        assert gradients.ndim == 2
        px, dpdx = self._process_input(images)
        g, = self._bw_grad_fn([gradients, px])
        g = self._process_gradient(dpdx, g)
        assert g.shape == images.shape
        return g
