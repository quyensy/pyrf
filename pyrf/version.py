VERSION = (2, 7, 2, 'dev')
__version__ = ''.join(['-.'[type(x) == int]+str(x) for x in VERSION])[1:]
