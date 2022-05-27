import errno
import sdl2
import sdl2.ext
import ctypes
import olympe_deps as od
import time
from abc import ABC, abstractmethod
from OpenGL import GL
from OpenGL import GLX
from olympe.log import LogMixin
from olympe.utils import callback_decorator
from olympe.concurrent import Loop


class Renderer(ABC, LogMixin):
    """
    An SDL2 OpenGL capable window renderer class
    """

    OPENGL_VERSION = (3, 3)
    DEFAULT_RESOLUTION = (800, 600)

    def __init__(
        self,
        name=None,
        device_name=None,
        window_title=None,
        width=None,
        height=None,
        **kwds
    ):

        super().__init__(name, device_name, "video.renderer")
        self._loop = Loop(self.logger)
        if window_title is None:
            window_title = "Olympe Video Renderer"
        if width is None:
            width = self.DEFAULT_RESOLUTION[0]
        if height is None:
            height = self.DEFAULT_RESOLUTION[1]
        self._window_title = window_title
        self._width = width
        self._height = height
        self._timer = None
        self._window = None
        self._glcontext = None
        self._init_kwds = kwds
        initialized = self._loop.run_async(self._async_init)
        self._loop.register_cleanup(self._dispose)
        self._loop.start()
        initialized.result_or_cancel(timeout=5.0)

    def _async_init(self):
        # Create an SDL2 window
        try:
            sdl2.ext.init()
        except sdl2.ext.common.SDLError as e:
            self.logger.error(str(e))
            return
        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
            raise RuntimeError(sdl2.SDL_GetError())
        self._window = sdl2.SDL_CreateWindow(
            self._window_title.encode(),
            sdl2.SDL_WINDOWPOS_UNDEFINED,
            sdl2.SDL_WINDOWPOS_UNDEFINED,
            self.width,
            self.height,
            sdl2.SDL_WINDOW_OPENGL
            | sdl2.SDL_WINDOW_RESIZABLE
            | sdl2.SDL_WINDOW_UTILITY,
        )

        # Create an OpenGL context
        sdl2.video.SDL_GL_SetAttribute(
            sdl2.video.SDL_GL_CONTEXT_MAJOR_VERSION, self.OPENGL_VERSION[0]
        )
        sdl2.video.SDL_GL_SetAttribute(
            sdl2.video.SDL_GL_CONTEXT_MINOR_VERSION, self.OPENGL_VERSION[1]
        )
        sdl2.video.SDL_GL_SetAttribute(
            sdl2.video.SDL_GL_CONTEXT_PROFILE_MASK,
            sdl2.video.SDL_GL_CONTEXT_PROFILE_CORE,
        )
        self._glcontext = sdl2.SDL_GL_CreateContext(self._window)
        sdl2.SDL_GL_MakeCurrent(self._window, self._glcontext)

        # Activate vertical synchronization
        sdl2.SDL_GL_SetSwapInterval(1)

        # Set the GLX context
        GLX.glXMakeCurrent(self.x11display, self.x11window, self.glx_context)

        # Call subclass custom initialization
        self.init(**self._init_kwds)

        # Start rendering
        sdl2.SDL_ShowWindow(self._window)
        self._period = float(1.0 / 60.0)
        self._loop.run_delayed(self._period, self._on_update)

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    @property
    def gl_context(self):
        return self._glcontext

    @property
    def glx_context(self):
        return ctypes.cast(
            ctypes.addressof(ctypes.c_int(self._glcontext)), GLX.GLXContext
        )

    @property
    def sdl_window(self):
        return self._window

    @property
    def _x11info(self):
        info = sdl2.SDL_SysWMinfo()
        sdl2.SDL_GetWindowWMInfo(self._window, ctypes.byref(info))
        return info.info.x11

    @property
    def x11display(self):
        """
        rtype: ctypes.POINTER(libX11.Display)
        """
        return self._x11info.display

    @property
    def x11window(self):
        """
        rtype: libX11.Window
        """
        return self._x11info.window

    def init(self):
        """
        This method is called from the Renderer base class constructor (__init__)
        after the OpenGL context has been created.
        and may be overridden in subclass to perform some custom initialization
        """

    @abstractmethod
    def render(self):
        """
        This method must be implemented in subclasses. SDL2 / OpenGL rendering
        must be performed from this method.
        """

    def stop(self):
        """
        Stops this renderer and close its associated window
        """
        self._loop.stop()

    @callback_decorator()
    def _on_update(self, *args):
        start_time = time.monotonic()
        events = sdl2.ext.get_events()
        for event in events:
            if event.type == sdl2.SDL_QUIT:
                self._loop.stop()
                return

        # Clear context
        GL.glClearColor(0, 0, 0, 1)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)

        self.render()
        sdl2.SDL_GL_SwapWindow(self._window)
        duration = time.monotonic() - start_time
        next_time = max(self._period - duration, 0.001)
        self._loop.run_delayed(next_time, self._on_update)

    @callback_decorator()
    def _dispose(self):
        self.dispose()

    def dispose(self):
        if self._window is not None:
            sdl2.SDL_HideWindow(self._window)

        if self._timer is not None:
            self._loop.destroy_timer(self._timer)
        self._timer = None

        if self._window is not None:
            sdl2.SDL_DestroyWindow(self._window)
        self._window = None

        if self._glcontext is not None:
            sdl2.SDL_GL_DeleteContext(self._glcontext)
        self._glcontext = None


class PdrawRenderer(Renderer):

    OPENGL_VERSION = (3, 0)

    def __init__(self, *args, **kwds):
        # When the async_init fails early (e.g. when there is no display available) the following
        # attributes must have been initialized because they are checked inside the render() method
        # before the actual rendering.
        self._media_infos = dict()
        self._pdraw_renderer = od.POINTER_T(od.struct_pdraw_video_renderer)()
        super().__init__(*args, **kwds)

    def init(self, *, pdraw, media_id=0):
        self._pdraw = pdraw
        self._media_id = media_id
        self._render_zone = od.struct_pdraw_rect(0, 0, self.width, self.height)
        self._renderer_params = od.struct_pdraw_video_renderer_params.bind(
            {
                "fill_mode": od.PDRAW_VIDEO_RENDERER_FILL_MODE_FIT_PAD_BLUR_EXTEND,
                "enable_transition_flags": od.PDRAW_VIDEO_RENDERER_TRANSITION_FLAG_ALL,
                "enable_hmd_distortion_correction": 0,
                "video_scale_factor": 1.0,
                "enable_overexposure_zebras": 0,
                "overexposure_zebras_threshold": 1.0,
                "enable_histograms": 0,
                "video_texture_width": self.width,
                "video_texture_dar_width": self.width,
                "video_texture_dar_height": self.height,
            }
        )

        self._renderer_cbs = od.struct_pdraw_video_renderer_cbs.bind(
            {
                "media_added": self._media_added_cb,
                "media_removed": self._media_removed_cb,
                "render_ready": self._render_ready_cb,
                # explicitly set to NULL is important here
                # to disable external texture loading
                "load_texture": None,
                "render_overlay": None,
            }
        )
        od.pdraw_video_renderer_new(
            self._pdraw.pdraw,
            self._media_id,
            self._render_zone,
            self._renderer_params,
            self._renderer_cbs,
            None,
            ctypes.byref(self._pdraw_renderer),
        )

    def render(self):
        if not self._media_infos:
            return
        if not self._pdraw_renderer:
            return
        content_pos = od.struct_pdraw_rect(0, 0, self.width, self.height)
        od.pdraw_video_renderer_render(
            self._pdraw.pdraw, self._pdraw_renderer, ctypes.byref(content_pos)
        )

    @callback_decorator()
    def _media_added_cb(self, pdraw, renderer, media_info, userdata):
        media_info = od.struct_pdraw_media_info.as_dict(media_info.contents)
        self._media_infos[media_info["id"]] = media_info

    @callback_decorator()
    def _media_removed_cb(self, pdraw, renderer, media_info, userdata):
        media_info = od.struct_pdraw_media_info.as_dict(media_info.contents)
        del self._media_infos[media_info["id"]]

    @callback_decorator()
    def _render_ready_cb(self, pdraw, renderer, userdata):
        return

    @callback_decorator()
    def _load_texture_cb(
        self,
        pdraw,
        renderer,
        texture_width,
        texture_height,
        media_info,
        frame,
        frame_user_data,
        famre_userdata_len,
        userdata,
    ):
        return -errno.ENOSYS

    @callback_decorator()
    def _render_overlay_cb(
        self,
        pdraw,
        renderer,
        render_pos,
        content_pos,
        view_mat,
        proj_mat,
        media_info,
        frame_meta,
        frame_extra,
        userdata,
    ):
        return

    def dispose(self):
        if self._pdraw_renderer:
            od.pdraw_video_renderer_destroy(self._pdraw.pdraw, self._pdraw_renderer)
        self._pdraw_renderer = od.POINTER_T(od.struct_pdraw_video_renderer)()
        self._media_infos = dict()
        super().dispose()


class TestRenderer(Renderer):
    OPENGL_VERSION = (3, 0)

    def init(self):
        GL.glMatrixMode(GL.GL_PROJECTION | GL.GL_MODELVIEW)
        GL.glLoadIdentity()
        GL.glOrtho(-400, 400, 300, -300, 0, 1)

    def render(self):
        x = 0.0
        y = 30.0
        GL.glRotatef(10.0, 0.0, 0.0, 1.0)
        GL.glBegin(GL.GL_TRIANGLES)
        GL.glColor3f(1.0, 0.0, 0.0)
        GL.glVertex2f(x, y + 90.0)
        GL.glColor3f(0.0, 1.0, 0.0)
        GL.glVertex2f(x + 90.0, y - 90.0)
        GL.glColor3f(0.0, 0.0, 1.0)
        GL.glVertex2f(x - 90.0, y - 90.0)
        GL.glEnd()
