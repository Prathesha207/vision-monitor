import os


def ensure_directory(path):

    if not os.path.exists(path):
        os.makedirs(path)