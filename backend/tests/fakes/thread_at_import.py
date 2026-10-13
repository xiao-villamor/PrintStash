"""A module that leaves a thread running once imported.

Stands in for a library a worker template must refuse to preload: a fork
copies only the thread that calls it, so every worker forked afterwards would
inherit the locks that thread held and none of the work it was doing.
"""

import threading

_stop = threading.Event()
threading.Thread(target=_stop.wait, name="left-behind", daemon=True).start()
