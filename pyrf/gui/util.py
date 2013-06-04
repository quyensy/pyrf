
from control_util import *
import numpy as np
def frequency_text(hz):
    """
    return hz as readable text in Hz, kHz, MHz or GHz
    """
    if hz < 1e3:
        return "%.3f Hz" % hz
    elif hz < 1e6:
        return "%.3f kHz" % (hz / 1e3)
    elif hz < 1e9:
        return "%.3f MHz" % (hz / 1e6)
    return "%.3f GHz" % (hz / 1e9)
    
def hotkey_util(layout,event):
    """
    modify elements in the gui layout based on which key was pressed
    """
    if arrow_dict.has_key(str(event.key())):
        hotkey =  arrow_dict[str(event.key())]
    else:
        hotkey = str(event.text()).upper()
    if hotkey_dict.has_key(hotkey):
        hotkey_dict[hotkey](layout)
        
def find_max_index(array):
    """
    returns the maximum index of an array         
    """
    # keep track of max index
    index = 0
    
    array_size = len(array)
    
    max_value = 0
    for i in range(array_size):
        
        if i == 0:
            max_value = array[i]
            index = i
        elif array[i] > max_value:
            max_value = array[i]
            index = i
    return index

def find_nearest_index(value, array):
    """
    returns the index in the array of the nearest value      
    """
    idx = (np.abs(array-value)).argmin()
    return idx
    
def html_text_conv(text,size,color):
    """
    returns the given text in an HTML format with proper format     
    """

      



