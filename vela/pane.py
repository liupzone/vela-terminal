"""Split-pane layout.

The tree is plain data (:class:`Leaf` / :class:`Split`) so it can be unit tested
without a display; :class:`PaneContainer` renders it with ``Gtk.Paned``.
"""

from __future__ import annotations

from typing import Callable, Iterator, List, Optional, Tuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402


class Node:
    """Base class for pane tree nodes."""

    parent: Optional["Split"] = None


class Leaf(Node):
    """One pane: a terminal, or a side panel such as the system monitor.

    The tree itself is type-agnostic — it only needs a widget that understands
    ``set_focused()`` — so a leaf records which kind of view it holds purely so
    the window can decide whether terminal-specific actions apply.
    """

    def __init__(self, view, kind: str = "terminal") -> None:
        self.view = view
        self.kind = kind
        self.parent: Optional[Split] = None

    def leaves(self) -> Iterator["Leaf"]:
        yield self

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Leaf({getattr(self.view, 'title', '?')})"


class Split(Node):
    """Two children side by side (horizontal) or stacked (vertical)."""

    def __init__(self, orientation: Gtk.Orientation, first: Node, second: Node) -> None:
        self.orientation = orientation
        self.first = first
        self.second = second
        self.parent: Optional[Split] = None
        self.position = 0
        # Auto-created splits stay centred (and follow window resizes) until the
        # user drags the handle, at which point their position is preserved.
        self.user_adjusted = False
        self.dragging = False
        self._applying_position = False
        self._position_handler = 0
        first.parent = self
        second.parent = self

    def leaves(self) -> Iterator[Leaf]:
        yield from self.first.leaves()
        yield from self.second.leaves()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        axis = "h" if self.orientation == Gtk.Orientation.HORIZONTAL else "v"
        return f"Split({axis}, {self.first!r}, {self.second!r})"


class PaneContainer(Gtk.Box):
    """Renders a pane tree and keeps the widget hierarchy in sync."""

    def __init__(
        self,
        make_view: Callable[..., object],
        on_layout_changed=None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._make_view = make_view
        self._on_layout_changed = on_layout_changed or (lambda: None)
        self.root: Optional[Node] = None
        self.active: Optional[Leaf] = None
        self._focused_widget: Optional[Gtk.Widget] = None
        self.zoomed = False
        # Two stacked hosts: the normal layout tree, and a full-size host used
        # while a single pane is zoomed.  Swapping which one is visible keeps
        # zoom a pure show/hide operation, so no widget is ever reparented.
        self._layout_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._zoom_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._zoom_host.set_no_show_all(True)
        super().pack_start(self._layout_host, True, True, 0)
        super().pack_start(self._zoom_host, True, True, 0)

    # -- construction ----------------------------------------------------
    def bootstrap(self, kind: str = "terminal") -> Leaf:
        """Create the first leaf."""
        leaf = Leaf(self._make_view(kind), kind)
        self.root = leaf
        widget = self._widget_for(leaf)
        self._layout_host.pack_start(widget, True, True, 0)
        self._layout_host.show_all()
        self.set_active(leaf)
        return leaf

    def leaves(self) -> List[Leaf]:
        return list(self.root.leaves()) if self.root else []

    def count(self) -> int:
        return len(self.leaves())

    # -- mutation --------------------------------------------------------
    def split(
        self,
        leaf: Leaf,
        orientation: Gtk.Orientation,
        cwd: Optional[str] = None,
        kind: str = "terminal",
    ) -> Leaf:
        """Split ``leaf``, creating a new pane of ``kind`` in the new half."""
        if self.root is None or leaf not in self.leaves():
            raise ValueError("cannot split a leaf that is not in this container")
        self.unzoom()
        new_leaf = Leaf(self._make_view(kind), kind)
        parent = leaf.parent
        split_node = Split(orientation, leaf, new_leaf)
        split_node.parent = parent
        if parent is None:
            self.root = split_node
            self._rebuild()
        else:
            if parent.first is leaf:
                parent.first = split_node
            else:
                parent.second = split_node
            # Rebuild from the root: the tree shape changed, and rendering is
            # cheap enough that incremental surgery is not worth the risk.
            self._rebuild()
        view = new_leaf.view
        if hasattr(view, "spawn"):
            view.spawn(cwd=cwd)
        # A panel pane starts its own work (the monitor's sampling timer) only
        # once it is on screen.
        starter = getattr(view, "start", None)
        if starter is not None:
            starter()
        self._on_layout_changed()
        self.set_active(new_leaf)
        # A split created while other parts of the window are mid-layout can end
        # up with a stale (1px) allocation; one explicit resize request after the
        # tree is rebuilt gives GTK the chance to size the new paned.
        GLib.idle_add(self._nudge_layout)
        return new_leaf

    def _nudge_layout(self) -> bool:
        # Runs from an idle callback, so the tree may already have changed (a
        # split can be closed before this fires).  Every access is guarded.
        try:
            self.queue_resize()
            self.queue_resize_no_redraw()
            for leaf in self.leaves():
                widget = self._widget_for(leaf)
                if widget is not None and widget.get_parent() is not None:
                    widget.queue_resize()
        except (AttributeError, TypeError, RuntimeError):  # pragma: no cover
            return False
        return False

    def close(self, leaf: Leaf) -> Optional[Leaf]:
        """Remove ``leaf``; returns the leaf that should get focus."""
        if self.root is None:
            return None
        if isinstance(self.root, Leaf):
            if self.root is leaf:
                self.root = None
                self._destroy_layout()
                self.active = None
                self._on_layout_changed()
                return None
            return self.active
        self.unzoom()
        parent = leaf.parent
        if parent is None:
            return self.active
        sibling = parent.second if parent.first is leaf else parent.first
        grandparent = parent.parent
        sibling.parent = grandparent
        if grandparent is None:
            self.root = sibling
        else:
            if grandparent.first is parent:
                grandparent.first = sibling
            else:
                grandparent.second = sibling
        self._rebuild()
        self._discard_widget(leaf)
        next_leaf = self._first_leaf(sibling)
        if self.active is leaf:
            # Route through set_active so the focus ring moves to the surviving
            # pane; assigning self.active directly left the closed pane's
            # neighbour without any highlight.
            self.set_active(next_leaf, focus=False)
        self._on_layout_changed()
        return self.active

    def replace_view(self, leaf: Leaf, view) -> None:
        """Swap the view inside a leaf, keeping its place in the tree.

        Used by "reopen closed tab" and by switching a pane's kind.  The new
        view is started here for the same reason ``split()`` does it: a panel
        pane only begins its work (the monitor's sampling timer) once it is on
        screen, and without this a pane switched to the system monitor showed
        placeholder dashes forever.
        """
        leaf.view = view
        self._rebuild()
        starter = getattr(view, "start", None)
        if starter is not None:
            starter()
        if self.active is leaf:
            self.set_active(leaf, focus=False)

    # -- restoring a saved layout ----------------------------------------
    def clear(self) -> None:
        """Tear the tree down, stopping every pane first.

        Used before rebuilding from a saved layout.  Panels are stopped so a
        discarded monitor does not keep its sampling timer alive.
        """
        for leaf in self.leaves():
            terminator = getattr(leaf.view, "terminate", None)
            if terminator is not None:
                terminator()
        self.root = None
        self.active = None
        self.zoomed = False
        self._focused_widget = None
        self._destroy_layout()
        self._zoom_host.hide()

    def build_from_spec(self, spec, make_view: Callable[[str], object]) -> Optional[Leaf]:
        """Rebuild the pane tree from a saved layout spec.

        ``make_view`` receives the pane kind and returns the view, so the
        container stays unaware of terminals and panels.
        """
        node = self._node_from_spec(spec, make_view)
        self.root = node
        self._rebuild()
        self._apply_positions(node)
        first = self._first_leaf(node)
        if first is not None:
            self.set_active(first)
        return first

    def _node_from_spec(self, spec, make_view: Callable[[str], object]) -> Node:
        """Turn a spec node into a tree of leaves, creating the views."""
        # A spec split carries its own type; detect it structurally so pane.py
        # does not have to import the layout module.
        if hasattr(spec, "first") and hasattr(spec, "second"):
            orientation = (
                Gtk.Orientation.HORIZONTAL
                if getattr(spec, "orientation", "h") == "h"
                else Gtk.Orientation.VERTICAL
            )
            node = Split(
                orientation,
                self._node_from_spec(spec.first, make_view),
                self._node_from_spec(spec.second, make_view),
            )
            node.position = int(getattr(spec, "position", 0) or 0)
            node.user_adjusted = bool(node.position)
            return node
        kind = getattr(spec, "kind", "terminal")
        view = make_view(kind)
        leaf = Leaf(view, kind)
        # A restored pane has to be brought to life here.  ``split()`` spawns the
        # shell of a pane it creates, and that step was missing on the restore
        # path, so every terminal came back as an empty frame with no prompt.
        cwd = getattr(spec, "cwd", "") or ""
        if hasattr(view, "spawn"):
            view.spawn(cwd=cwd or None)
        else:
            starter = getattr(view, "start", None)
            if starter is not None:
                starter()
        return leaf

    def _apply_positions(self, node: Optional[Node]) -> None:
        """Re-apply saved divider positions once the panes have a size."""
        if node is None or isinstance(node, Leaf):
            return
        if node.position:
            paned = getattr(node, "widget", None)
            if paned is not None:
                paned.set_position(int(node.position))
        self._apply_positions(node.first)
        self._apply_positions(node.second)

    # -- focus -----------------------------------------------------------
    def set_active(self, leaf: Optional[Leaf], focus: bool = True) -> None:
        if leaf is None:
            self.active = None
            return
        if self.active is not None and self.active is not leaf:
            self._set_leaf_focused(self.active, False)
        self.active = leaf
        self._set_leaf_focused(leaf, True)
        if focus:
            self.focus_active()

    def _set_leaf_focused(self, leaf: Leaf, focused: bool) -> None:
        """Toggle a pane's focus ring.

        The view draws the ring itself (see ``TerminalView.set_focused``); the
        CSS class is kept in sync so styling hooks and tests still see it.
        """
        widget = self._widget_for(leaf)
        if widget is None:
            return
        context = widget.get_style_context()
        if focused:
            context.add_class("focused")
        else:
            context.remove_class("focused")
        setter = getattr(leaf.view, "set_focused", None)
        if setter is not None:
            setter(focused)

    def focus_active(self) -> None:
        if self.active is None:
            return
        widget = self._widget_for(self.active)
        if widget is None:
            return
        target = getattr(self.active.view, "terminal", None)
        if self.zoomed:
            self._widget_for(self.active).grab_focus()
            if target is not None:
                target.grab_focus()
            return
        if target is not None:
            target.grab_focus()
        else:  # pragma: no cover - defensive
            widget.grab_focus()

    def focus_direction(self, direction: str) -> bool:
        """Move focus to the nearest pane in ``left/right/up/down``."""
        if self.zoomed or self.active is None:
            return False
        current = self._allocation(self.active)
        if current is None:
            return False
        best: Optional[Tuple[float, Leaf]] = None
        cx, cy = current[0] + current[2] / 2, current[1] + current[3] / 2
        for leaf in self.leaves():
            if leaf is self.active:
                continue
            box = self._allocation(leaf)
            if box is None:
                continue
            ox, oy = box[0] + box[2] / 2, box[1] + box[3] / 2
            dx, dy = ox - cx, oy - cy
            if direction == "left" and dx > -1:
                continue
            if direction == "right" and dx < 1:
                continue
            if direction == "up" and dy > -1:
                continue
            if direction == "down" and dy < 1:
                continue
            primary = abs(dx) if direction in ("left", "right") else abs(dy)
            secondary = abs(dy) if direction in ("left", "right") else abs(dx)
            score = primary + secondary * 0.5
            if best is None or score < best[0]:
                best = (score, leaf)
        if best is None:
            return False
        self.set_active(best[1])
        return True

    def cycle(self, step: int = 1) -> None:
        leaves = self.leaves()
        if len(leaves) < 2 or self.active not in leaves:
            return
        index = (leaves.index(self.active) + step) % len(leaves)
        self.set_active(leaves[index])

    # -- zoom ------------------------------------------------------------
    def toggle_zoom(self) -> bool:
        if self.zoomed:
            self.unzoom()
            return False
        if self.active is None or self.count() < 2:
            return False
        self._focused_widget = self._widget_for(self.active)
        if self._focused_widget is None:
            return False
        parent = self._focused_widget.get_parent()
        if parent is not None:
            parent.remove(self._focused_widget)
        self._zoom_host.pack_start(self._focused_widget, True, True, 0)
        self.zoomed = True
        self._zoom_host.show()
        self._focused_widget.show_all()
        self._layout_host.hide()
        self.focus_active()
        self._on_layout_changed()
        return True

    def unzoom(self) -> bool:
        if not self.zoomed or self.root is None:
            return False
        widget = self._focused_widget
        self.zoomed = False
        self._focused_widget = None
        if widget is not None:
            parent = widget.get_parent()
            if parent is not None:
                parent.remove(widget)
        self._zoom_host.hide()
        self._layout_host.show()
        self._rebuild()
        if self.active is not None:
            self.set_active(self.active, focus=False)
        self._on_layout_changed()
        return True

    # -- rendering -------------------------------------------------------
    def _widget_for(self, node: Optional[Node]) -> Optional[Gtk.Widget]:
        if node is None:
            return None
        if isinstance(node, Leaf):
            view = node.view
            return view if isinstance(view, Gtk.Widget) else getattr(view, "widget", None)
        widget = getattr(node, "widget", None)
        return widget

    def _allocation(self, leaf: Leaf):
        """Pane geometry in *window* coordinates.

        ``get_allocation()`` returns coordinates relative to each widget's own
        parent, and two panes usually have different parents, so those values
        cannot be compared directly: doing so made directional focus pick the
        wrong pane (or none at all).  Translating to the top-level window puts
        every pane in one coordinate space.
        """
        widget = self._widget_for(leaf)
        if widget is None:
            return None
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        if width <= 1 and height <= 1:
            return None
        origin = self.get_toplevel()
        if not isinstance(origin, Gtk.Widget):
            alloc = widget.get_allocation()
            return (alloc.x, alloc.y, width, height)
        translated = widget.translate_coordinates(origin, 0, 0)
        if translated is None or len(translated) != 2:
            alloc = widget.get_allocation()
            return (alloc.x, alloc.y, width, height)
        x, y = translated
        return (x, y, width, height)

    def _render_node(self, node: Node, container: Gtk.Container) -> None:
        """Put ``node``'s widget inside ``container`` and fill its children."""
        if isinstance(node, Leaf):
            widget = self._widget_for(node)
            if widget is not None and widget.get_parent() is not container:
                if widget.get_parent() is not None:
                    widget.get_parent().remove(widget)
                self._place(container, widget)
            return
        paned = self._ensure_paned(node)
        if paned.get_parent() is not container:
            if paned.get_parent() is not None:
                paned.get_parent().remove(paned)
            self._place(container, paned)
        self._fill_split(node)
        # A GtkPaned that was shown before both slots were filled caches a 1px
        # minimum size, so the split collapses.  Hiding and re-showing it after
        # the children are packed forces the size request to be recomputed.
        paned.hide()
        paned.show_all()

    def _ensure_paned(self, node: Split) -> Gtk.Paned:
        """Create (recursively) the GtkPaned backing ``node`` and its children."""
        paned = getattr(node, "widget", None)
        if paned is None:
            paned = Gtk.Paned.new(node.orientation)
            paned.get_style_context().add_class("vela-paned")
            paned.set_wide_handle(False)
            paned.connect(
                "notify::position",
                lambda widget, _param, n=node: self._remember_position(
                    n, widget.get_position()
                ),
            )
            # GtkPaned exposes no "handle dragged" signal, so the drag is
            # inferred from the button being held on the handle (which lives in
            # the paned's own GdkWindow).
            paned.connect(
                "button-press-event",
                lambda _w, event, n=node: self._begin_drag(n, event),
            )
            paned.connect(
                "button-release-event",
                lambda _w, event, n=node: self._end_drag(n, event),
            )
            node.widget = paned
        for child in (node.first, node.second):
            if isinstance(child, Split):
                self._ensure_paned(child)
        return paned

    @staticmethod
    def _place(container: Gtk.Container, widget: Gtk.Widget) -> None:
        """Add ``widget`` to ``container`` filling the available space.

        ``Gtk.Box.add()`` packs without expansion, which would leave a pane at
        its minimum size, so boxes get an explicit expanding ``pack_start``.
        """
        PaneContainer._detach(widget, container)
        if isinstance(container, Gtk.Box):
            container.pack_start(widget, True, True, 0)
        else:
            container.add(widget)

    @staticmethod
    def _detach(widget: Gtk.Widget, target: Optional[Gtk.Container]) -> None:
        """Remove ``widget`` from whatever currently owns it."""
        parent = widget.get_parent()
        if parent is not None and parent is not target:
            parent.remove(widget)

    def _fill_split(self, node: Split) -> None:
        """Populate both slots of ``node``'s GtkPaned (never reparenting itself)."""
        paned = getattr(node, "widget", None)
        if paned is None:
            return
        for child, first in ((node.first, True), (node.second, False)):
            slot = paned.get_child1() if first else paned.get_child2()
            widget = self._widget_for(child)
            if widget is None:
                continue
            if slot is not widget:
                if slot is not None:
                    paned.remove(slot)
                self._detach(widget, paned)
                if first:
                    paned.pack1(widget, True, True)
                else:
                    paned.pack2(widget, True, True)
            if isinstance(child, Split):
                self._fill_split(child)
            widget.show_all()
        # A user-dragged divider keeps its position; an untouched one is centred
        # on every allocation so it adapts when the window is resized.
        if node.user_adjusted:
            paned.set_position(node.position)
        else:
            self._centre_when_allocated(node)

    def _centre_when_allocated(self, node: Split) -> None:
        """Set a 50/50 split as soon as the paned has a usable size.

        A freshly built ``Gtk.Paned`` reports 1x1 during the build, so deciding
        the divider position there would leave it at the child's minimum size
        (34px wide in practice) and the new pane would be unusable.
        """
        paned = getattr(node, "widget", None)
        if paned is None:
            return

        def apply_position(*_args) -> None:
            if node.user_adjusted:
                return
            allocation = paned.get_allocation()
            size = (
                allocation.width
                if node.orientation == Gtk.Orientation.HORIZONTAL
                else allocation.height
            )
            if size <= 40:
                return
            target = size // 2
            # Record the value we intend to set *before* writing it: GtkPaned
            # emits notify::position synchronously inside set_position(), and the
            # handler compares against this to tell our write from a user drag.
            node.position = target
            if paned.get_position() == target:
                return
            # Mark the write as ours: the notify handler must not mistake it for
            # a user drag, which would freeze the divider off-centre.
            node._applying_position = True
            try:
                paned.set_position(target)
            finally:
                node._applying_position = False

        node._position_handler = paned.connect("size-allocate", apply_position)
        apply_position()

    def _remember_position(self, node: Split, position: int) -> None:
        if node._applying_position:
            return
        if node.user_adjusted:
            node.position = position
            return
        if position <= 0:
            # GtkPaned reports 0 before it has been sized; that is not a drag.
            return
        if not node.dragging:
            # GtkPaned also emits this during its own layout passes (and after
            # our centring write), so a position change on its own is not proof
            # of a drag.  Only a change that happens while the handle is held
            # counts, otherwise auto-created splits would freeze off-centre.
            return
        node.user_adjusted = True
        node.position = position

    def _begin_drag(self, node: Split, event) -> bool:
        """A press on the handle starts a user drag."""
        if event.button == 1:
            node.dragging = True
        return False

    def _end_drag(self, node: Split, event) -> bool:
        """Releasing the handle ends the drag and locks in the position."""
        if event.button == 1:
            node.dragging = False
            widget = getattr(node, "widget", None)
            if widget is not None and widget.get_position() > 0:
                node.user_adjusted = True
                node.position = widget.get_position()
        return False

    def _rebuild(self) -> None:
        """Discard the rendered tree and rebuild it from ``self.root``."""
        # Discard every GtkPaned and build fresh ones.  This GTK version caches a
        # GtkPaned's minimum size when its children are replaced while it is on
        # screen, and the cached value is 1px, which collapsed whole splits.  A
        # new paned negotiates its size correctly.
        self._destroy_layout()
        self._forget_paneds(self.root)
        if self.root is None:
            return
        self._render_node(self.root, self._layout_host)
        self._layout_host.show_all()
        if not self.zoomed:
            self.show_all()
            self._zoom_host.hide()
        # Re-run the size negotiation from the top: without this the freshly
        # packed paned keeps the 1px allocation it was given before its children
        # existed.
        self._layout_host.queue_resize()
        self.queue_resize()
        # Closing a pane rebuilds every GtkPaned; the new ones need one more
        # negotiation pass before the survivors report a real size, otherwise a
        # just-closed layout shows every pane as 1x1.
        GLib.idle_add(self._settle_layout)
        if self.active is not None:
            self.set_active(self.active, focus=False)

    def _settle_layout(self) -> bool:
        """Ask for a final resize once the rebuilt tree is idle."""
        if not self.get_realized():
            return False
        self.queue_resize()
        for leaf in self.leaves():
            widget = self._widget_for(leaf)
            if widget is not None and widget.get_parent() is not None:
                widget.queue_resize()
        return False

    def _forget_paneds(self, node: Optional[Node]) -> None:
        if node is None or isinstance(node, Leaf):
            return
        node.widget = None
        node._position_handler = 0
        self._forget_paneds(node.first)
        self._forget_paneds(node.second)

    def _destroy_layout(self) -> None:
        """Tear down the rendered tree, keeping the terminal views alive.

        Removing a GtkPaned drops the last reference to it, which destroys the
        paned *and its children*.  The terminal views are still owned by their
        Leaf nodes and get packed into fresh panes right after, so they must be
        detached before the paned goes away — re-using a destroyed widget is what
        made GTK crash inside gtk_widget_get_preferred_height().

        Only the panes' own child widgets are detached, never their internals:
        emptying a TerminalView would pull the VTE widget out of its scroller,
        and nothing puts it back, leaving that pane permanently unrealized (it
        rendered as an empty box and could not take keyboard focus).
        """
        for child in list(self._layout_host.get_children()):
            self._detach_children(child)
            self._layout_host.remove(child)

    def _detach_children(self, widget: Gtk.Widget) -> None:
        """Remove a paned's direct children so they survive its destruction.

        Panes below ``widget`` are visited, but their contents are left alone.
        """
        if not isinstance(widget, Gtk.Paned):
            return
        for slot in (widget.get_child1(), widget.get_child2()):
            if slot is None:
                continue
            # Nested panes are detached the same way, but a TerminalView is a
            # leaf: it is removed whole, keeping its scroller and VTE intact.
            self._detach_children(slot)
            widget.remove(slot)

    def _discard_widget(self, leaf: Leaf) -> None:
        widget = self._widget_for(leaf)
        if widget is not None and widget.get_parent() is not None:
            widget.get_parent().remove(widget)

    def _first_leaf(self, node: Node) -> Leaf:
        if isinstance(node, Leaf):
            return node
        return self._first_leaf(node.first)
