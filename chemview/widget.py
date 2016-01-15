from __future__ import absolute_import

import json
from collections import defaultdict
from functools import partial
from uuid import uuid4

import numpy as np
from IPython.display import Image, Javascript, display
from ipywidgets.widgets import DOMWidget, widget_serialization
from traitlets import (Any, Bool, Bytes, CBool, CFloat, CInt, CUnicode, Dict,
                       Enum, List, Tuple, Unicode)

from .utils import encode_numpy

__all__ = ['RepresentationViewer', "TrajectoryControls"]


class RepresentationViewer(DOMWidget):

    # Name of the javascript class which this widget syncs against on the
    # browser side. To work correctly, this javascript class has to be
    # registered and loaded in the browser before this widget is constructed
    # (that's what enable_notebook() does)
    _view_module = Unicode('nbextensions/chemview_widget', sync=True)
    _view_name = Unicode('MolecularView', sync=True)

    width = CInt(sync=True)
    height = CInt(sync=True)
    background = CInt(sync=True)

    # Update Camera Hack
    camera_str = CUnicode(sync=True)
    static_moving = CBool(sync=True)

    # Helper
    loaded = CBool(False, sync=True)

    def __init__(self, width=500, height=500):
        '''RepresentationViewer is an IPython notebook widget useful to display 3d scenes through webgl.

        Example:

        .. code::

            from IPython.display import display

            rv = RepresentationViewer()
            rv.add_representation('point', {'coordinates': coordinates, 'colors': colors, 'sizes': sizes})
            display(rv)

        .. py:attribute: width

            Width in pixels of the IPython widget

        .. py:attribute: height

            Height in pixels of the IPython widget

        .. py:attribute: camera_str

            A string-representation of camera position and orientation

        .. py:attribute: static_moving

            Set to True to make the camera lose the "bouncy" rotation.


        '''
        super(RepresentationViewer, self).__init__()
        self.displayed = False
        self.width = width
        self.height = height

        # Store the events sent from the javascript side
        self._event_handlers = defaultdict(list)

        # What to do when we export
        def callback(content):
            display(Image(url=content.get('dataUrl')))
        self._connect_event('displayImg', callback)

        # A record of the new representations
        self.representations = {}

        # Things to be called when the js part is done loading
        self._displayed_callbacks = []
        def on_loaded(name, old, new):
            for cb in self._displayed_callbacks:
                cb(self)

        self.on_trait_change(on_loaded, "loaded")

    def add_representation(self, rep_type, options, rep_id=None):
        '''Add a 3D representation to the viewer.  See User Guide for
        a complete description of the representations available.

        :return: An unique hexadecimal identifier for the representation.
        :rtype: str

        '''
        # Add our unique id to be able to refer to the representation
        if rep_id is None:
            rep_id = uuid4().hex
        
        if rep_type in checkers:
            options = checkers[rep_type](options)

        self.representations[rep_id] = {'rep_type' : rep_type,
                                        'options': options.copy()}

        self._remote_call('addRepresentation', type=rep_type, repId=rep_id, options=options)
        return rep_id

    def remove_representation(self, rep_id):
        '''Remove a representation from the viewer

        :param str rep_id: the unique identifier generated by RepresentationViewer.add_representation

        '''
        self._remote_call('removeRepresentation', repId=rep_id)
        del self.representations[rep_id]

    def update_representation(self, rep_id, options):
        '''Update a representation with new data.

        :param str rep_id: the unique identifier returned by RepresentationViewer.add_representation
        :param dict options: dictionary containing the updated data.

        '''
        self.representations[rep_id]['options'].update(options)
        rep_type = self.representations[rep_id]["rep_type"]
        if rep_type in checkers:
            options = checkers[rep_type](options)
        self._remote_call('updateRepresentation', repId=rep_id, options=options)

    def _connect_event(self, event_name, callback):
        '''Respond to an event sent by the Javascript side.

        Events available:

            - displayImg
            - serialize
            - fullscreen


        '''
        self._event_handlers[event_name].append(callback)

    def _remote_call(self, method_name, **kwargs):
        '''Call a method remotely on the javascript side'''
        msg = {}
        msg['type'] = 'callMethod'
        msg['methodName'] = method_name
        msg['args'] = self._recursive_serialize(kwargs)

        if self.displayed is True:
            self.send(msg) # This will be received with View.on_msg
        else:
            # We should prepare a callback to be
            # called when widget is displayed
            def callback(widget, msg=msg):
                widget.send(msg)

            self._displayed_callbacks.append(callback)

    def _recursive_serialize(self, dictionary):
        '''Serialize a dictionary inplace'''
        for k, v in dictionary.items():
            if isinstance(v, dict):
                self._recursive_serialize(v)
            else:
                # This is when custom serialization happens
                if isinstance(v, np.ndarray):
                    if v.dtype == 'float64':
                        # We don't support float64 on js side
                        v = v.astype('float32')

                    dictionary[k] = encode_numpy(v)
        return dictionary

    def _handle_custom_msg(self, content, buffers=None):
        # Handle custom messages sent by the javascript counterpart
        event = content.get('event', '')
        for cb in self._event_handlers[event]:
            cb(content)


    def _ipython_display_(self, **kwargs):
        super(RepresentationViewer, self)._ipython_display_(**kwargs)
        self.displayed = True

    def get_scene(self):
        '''Return a dictionary that uniquely identifies the scene displayed'''

        scene = {}

        # Camera
        camspec = json.loads(self.camera_str)
        location = np.array([camspec['position']['x'],
                             camspec['position']['y'],
                             camspec['position']['z']], 'float')
        quaternion = np.array([camspec['quaternion']['_x'],
                               camspec['quaternion']['_y'],
                               camspec['quaternion']['_z'],
                               camspec['quaternion']['_w']], 'float')
        target = np.array([camspec['target']['x'],
                           camspec['target']['y'],
                           camspec['target']['z']], 'float')

        scene['camera'] = dict(location=location, quaternion=quaternion,
                               target=target, vfov=camspec['fov'],
                               aspect=camspec['aspect'])
        # Lights: TODO
        scene['lights'] = [ {'position': np.array([2, 4, -3]) * 1000,
                             'color': 0xffffff },
                            {'position': np.array([-1, 2, 3]) * 1000,
                             'color': 0xffffff } ]
        # Objects
        rep = {k: v.copy() for v in self.representations.items()}
        
        scene['representations'] = [v.update({"id" : k}) for k, v in rep.items()]
        scene['representations'] = [item.update({'id'})]
        scene['background'] = self.background

        return scene
    
    @classmethod
    def from_scene(cls, scenedict):
        self = cls()
        
        """Build a representation from scenedict"""
        for rep in scenedict["representations"]:
            self.add_representation(rep["rep_type"], rep["options"], rep['rep_id'])
        return self
            
            
def check_points(options):
    cleaned = {}
    cleaned["coordinates"] = np.ascontiguousarray(options["coordinates"], dtype="float32")
    if "sizes" in options:
        cleaned["sizes"] = list(options["sizes"])
    
    if "colors" in options:
        cleaned["colors"] = list(options["colors"])
    
    if options.get("visible", None) is not None:
        # Careful! np.bool_ is not serializable!
        cleaned["visible"] = [bool(i) for i in options["visible"]]
    
    return cleaned

checkers = {"points" : check_points }

class TrajectoryControls(DOMWidget):
    _view_module = Unicode('nbextensions/trajectory_controls_widget', sync=True)
    _view_name = Unicode('TrajectoryControls', sync=True)

    width = CInt(sync=True)
    
    frame = CInt(sync=True)
    n_frames = CInt(sync=True)
    fps = CInt(sync=True)
    
    def __init__(self, n_frames, fps=30, width=500):
        '''Play/Pause controls useful for playing trajectories.

        Example:

        You can connect a callback to be executed every time the frame changes.

        .. code::

            from IPython.display import display

            controls = TrajectoryControls(10) # 10 frames

            def callback(frame):
                print("Current frame %d" % frame)

            controls.on_frame_change(callback)
            display(controls)

        .. py:attribute:: frame

            Current frame

        .. py:attribute:: n_frames

            Total number of frames

        .. py:attribute:: fps

            Frames per second (defaults to 30)

        '''
        super(TrajectoryControls, self).__init__()
        self.n_frames = n_frames - 1
        self.fps = fps
        self.width = width
    
    def attach(self, event, widget):
        widget._connect_event("fullscreen", partial(self._handle_fullscreen, widget))
    
    def _handle_fullscreen(self, widget, content):
        self.send({"type": "callMethod", 
                   "methodName": "fullscreen",
                   "args": { "model_id": widget.model_id }})

    def on_frame_change(self, callback):
        '''Connect a callback to be executed every time the frame attribute changes.'''
        self.on_trait_change(lambda name, old, new: callback(new), "frame")

class Layout(DOMWidget):
    
    _view_module = Unicode("nbextensions/layout_widget", sync=True)
    _view_name = Unicode("Layout", sync=True)
    _model_name = Unicode("BoxModel", sync=True)
    
    children = Tuple(sync=True, **widget_serialization)
    # width = CInt(sync=True)
    # height = CInt(sync=True)
    
    def __init__(self, children, width=500, height=500):
        super(Layout, self).__init__()
        self.children = children
        # self.width = width
        # self.height = height


# Backporting some extra widgets

class FloatRangeWidget(DOMWidget):

    #_view_module = Unicode('nbextensions/floatrange_widget', sync=True)
    _view_name = Unicode('FloatRangeWidget', sync=True)

    value = Tuple(CFloat, CFloat, default_value=(0.0, 1.0),
                  help="Tuple of (lower, upper) bounds", sync=True)

    min = CFloat(sync=True)
    max = CFloat(sync=True)
    step = CFloat(sync=True)

    value_min = CFloat(sync=True)
    value_max = CFloat(sync=True)

    description = Unicode(sync=True)


    def __init__(self, min=0.0, max=1.0, step=0.1, value_min=0.0, value_max=1.0):
        super(FloatRangeWidget, self).__init__()

        self.min = min
        self.max = max
        self.step = step
        self.value_min = value_min
        self.value_max = value_max

        self.value = (self.value_min, self.value_max)

        self.on_trait_change(self.on_value_max_change, "value_max")
        self.on_trait_change(self.on_value_min_change, "value_min")

    def on_value_max_change(self, name, old, new):
        self.value = (self.value_min, self.value_max)

    def on_value_min_change(self, name, old, new):
        self.value = (self.value_min, self.value_max)
