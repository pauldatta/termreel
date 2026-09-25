"""
Composite a multi-pane tmux window into one TerminalState.

tmux's ``capture-pane`` only ever returns one pane. To record what a viewer
of the window would see, each pane is parsed into its own scratch grid and
copied into the main grid at the pane's offset, with tmux-style borders drawn
in the one-cell gaps between panes. The cursor comes from the active pane.
"""

from typing import Iterable, List

from termreel.emulator.parser import ANSIParser
from termreel.emulator.state import CharCell, Row, TerminalState

BORDER_V = "│"
BORDER_H = "─"
BORDER_X = "┼"


def composite_panes(state: TerminalState, panes: Iterable) -> None:
    """
    Replace ``state``'s active grid with the composited ``panes``.

    Each pane needs ``left``, ``top``, ``width``, ``height``, ``active``,
    ``cursor_x``, ``cursor_y``, ``cursor_visible`` and ``content`` (the
    ``capture-pane -p -e -J`` output for that pane). Caller holds the lock.
    """
    panes = list(panes)
    rows, cols = state.rows, state.cols
    if not panes:
        return

    if len(panes) == 1 and panes[0].left == 0 and panes[0].top == 0:
        # Single pane: parse straight into the state so soft-wrap flags on
        # full-width rows survive for wrap-aware masking.
        p = panes[0]
        ANSIParser(state).feed_tmux_pane(
            p.content, cursor=(p.cursor_x, p.cursor_y),
            cursor_visible=p.cursor_visible, joined=True,
        )
        return

    state.clear()
    grid = state.grid
    try:
        border_fg = state.palette.get_16_color(8)
    except Exception:
        border_fg = state.default_fg

    def put(r: int, c: int, ch: str) -> None:
        if 0 <= r < rows and 0 <= c < cols:
            existing = grid[r][c].char
            if (existing == BORDER_H and ch == BORDER_V) or (existing == BORDER_V and ch == BORDER_H):
                ch = BORDER_X
            grid[r][c] = CharCell(char=ch, fg=border_fg, bg=state.default_bg)

    active = None
    for p in panes:
        scratch = TerminalState(rows=max(1, p.height), cols=max(1, p.width),
                                default_fg=state.default_fg, default_bg=state.default_bg,
                                palette=state.palette)
        ANSIParser(scratch).feed_tmux_pane(p.content, joined=True)
        for r in range(min(p.height, rows - p.top)):
            src = scratch.grid[r]
            dst = grid[p.top + r]
            for c in range(min(p.width, cols - p.left)):
                dst[p.left + c] = src[c]
            # A soft wrap only means something for full-width rows; for a
            # narrower pane the "next row" in the composite is another pane.
            if p.left == 0 and p.width >= cols:
                dst.wrapped = src.wrapped
        if p.left > 0:
            for r in range(p.top, min(rows, p.top + p.height)):
                put(r, p.left - 1, BORDER_V)
        if p.top > 0:
            for c in range(p.left, min(cols, p.left + p.width)):
                put(p.top - 1, c, BORDER_H)
        if p.left > 0 and p.top > 0:
            put(p.top - 1, p.left - 1, BORDER_X)
        if p.active:
            active = p

    if active is None:
        active = panes[0]
    state.cursor.row = max(0, min(rows - 1, active.top + active.cursor_y))
    state.cursor.col = max(0, min(cols - 1, active.left + active.cursor_x))
    state.cursor.visible = bool(active.cursor_visible)
