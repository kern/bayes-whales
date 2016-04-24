import matplotlib
matplotlib.use('Agg')

import os
import time
import cPickle
import datetime
import logging
import flask
import werkzeug
import optparse
import tornado.wsgi
import tornado.httpserver
import numpy as np
import pandas as pd
import json
from PIL import Image
import cStringIO as StringIO
import urllib
import exifutil

import caffe

REPO_DIRNAME = './model'
UPLOAD_FOLDER = '/tmp/caffe_demos_uploads'
ALLOWED_IMAGE_EXTENSIONS = set(['png', 'bmp', 'jpg', 'jpe', 'jpeg', 'gif'])

# Obtain the flask app object
app = flask.Flask(__name__, static_url_path="", static_folder='/static')

# @app.route('/')
# def index():
#     return app.send_static_file('./static/index.html')

@app.route('/')
def root():
    return app.send_static_file('index.html')

@app.route('/predict', methods=['POST'])
def classify_url():
    if 'url' in flask.request.args:
        imageurl = flask.request.args.get('url', '')
        try:
            string_buffer = StringIO.StringIO(
                urllib.urlopen(imageurl).read())
            image = caffe.io.load_image(string_buffer)

        except Exception as err:
            # For any exception we encounter in reading the image, we will just
            # not continue.
            logging.info('URL Image open error: %s', err)
            return json.dumps({ 'accuracy': [], 'specificity': [] })

        logging.info('Image: %s', imageurl)
        result = app.clf.classify_image(image)
        accuracy = [{ 'label': label, 'score': score } for label, score in result[2]]
        return json.dumps({ 'image': embed_image_html(image), 'accuracy': accuracy, 'specificity': accuracy })
    else:
        try:
            # We will save the file to disk for possible data collection.
            imagefile = flask.request.files['file']
            filename_ = str(datetime.datetime.now()).replace(' ', '_') + \
                werkzeug.secure_filename(imagefile.filename)
            filename = os.path.join(UPLOAD_FOLDER, filename_)
            imagefile.save(filename)
            logging.info('Saving to %s.', filename)
            image = exifutil.open_oriented_im(filename)

        except Exception as err:
            logging.info('Uploaded image open error: %s', err)
            return json.dumps({ 'accuracy': [], 'specificity': [] })

        result = app.clf.classify_image(image)
        accuracy = [{ 'label': label, 'score': score } for label, score in result[2]]
        return json.dumps({ 'image': embed_image_html(image), 'accuracy': accuracy, 'specificity': accuracy })


def embed_image_html(image):
    """Creates an image embedded in HTML base64 format."""
    image_pil = Image.fromarray((255 * image).astype('uint8'))
    image_pil = image_pil.resize((400, 400))
    string_buf = StringIO.StringIO()
    image_pil.save(string_buf, format='png')
    data = string_buf.getvalue().encode('base64').replace('\n', '')
    return 'data:image/png;base64,' + data


def allowed_file(filename):
    return (
        '.' in filename and
        filename.rsplit('.', 1)[1] in ALLOWED_IMAGE_EXTENSIONS
    )


class ImagenetClassifier(object):
    default_args = {
        'model_def_file': (
            '{}/deploy.prototxt'.format(REPO_DIRNAME)),
        'pretrained_model_file': (
            '{}/weights.caffemodel'.format(REPO_DIRNAME)),
        'class_labels_file': (
            '{}/classes.txt'.format(REPO_DIRNAME))
    }
    for key, val in default_args.iteritems():
        if not os.path.exists(val):
            raise Exception(
                "File for {} is missing. Should be at: {}".format(key, val))

    def __init__(self, model_def_file, pretrained_model_file, 
                 class_labels_file, gpu_mode):
        logging.info('Loading net and associated files...')
        if gpu_mode:
            caffe.set_mode_gpu()
        else:
            caffe.set_mode_cpu()
        self.net = caffe.Classifier(
            model_def_file, pretrained_model_file,
            image_dims=(400, 400), raw_scale=400,
            mean=np.load('{}/mean.npy'.format(REPO_DIRNAME)).mean(1).mean(1), channel_swap=(2, 1, 0)
        )

        with open(class_labels_file) as f:
            labels_df = pd.DataFrame([
                {
                    'synset_id': l.strip().split(' ')[0],
                    'name': ' '.join(l.strip().split(' ')[1:]).split(',')[0]
                }
                for l in f.readlines()
            ])
        self.labels = labels_df.sort('synset_id')['name'].values

    def classify_image(self, image):
        try:
            starttime = time.time()
            scores = self.net.predict([image], oversample=True).flatten()
            endtime = time.time()

            indices = (-scores).argsort()[:5]
            predictions = self.labels[indices]

            # In addition to the prediction text, we will also produce
            # the length for the progress bar visualization.
            meta = [
                (p, '%.5f' % scores[i])
                for i, p in zip(indices, predictions)
            ]
            logging.info('result: %s', str(meta))

            # sort the scores
            # infogain_sort = expected_infogain.argsort()[::-1]
            # bet_result = [(self.bet['words'][v], '%.5f' % expected_infogain[v])
            #               for v in infogain_sort[:5]]
            # logging.info('bet result: %s', str(bet_result))

            return (True, meta, meta, '%.3f' % (endtime - starttime))

        except Exception as err:
            logging.info('Classification error: %s', err)
            return (False, 'Something went wrong when classifying the '
                           'image. Maybe try another one?')

def start_tornado(app, port=5000):
    http_server = tornado.httpserver.HTTPServer(
        tornado.wsgi.WSGIContainer(app))
    http_server.listen(port)
    print("Tornado server starting on port {}".format(port))
    tornado.ioloop.IOLoop.instance().start()


def start_from_terminal(app):
    """
    Parse command line options and start the server.
    """
    parser = optparse.OptionParser()
    parser.add_option(
        '-d', '--debug',
        help="enable debug mode",
        action="store_true", default=False)
    parser.add_option(
        '-p', '--port',
        help="which port to serve content on",
        type='int', default=5000)
    parser.add_option(
        '-g', '--gpu',
        help="use gpu mode",
        action='store_true', default=False)

    opts, args = parser.parse_args()
    ImagenetClassifier.default_args.update({'gpu_mode': opts.gpu})

    # Initialize classifier + warm start by forward for allocation
    app.clf = ImagenetClassifier(**ImagenetClassifier.default_args)
    # app.clf.net.forward()

    if opts.debug:
        app.run(debug=True, host='0.0.0.0', port=opts.port)
    else:
        start_tornado(app, opts.port)


if __name__ == '__main__':
    logging.getLogger().setLevel(logging.INFO)
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    start_from_terminal(app)
