import type { ITerminalOptions } from "@xterm/xterm";

export const TERMINAL_FONT_FAMILY = "ui-monospace, SFMono-Regular, Menlo, monospace";
export const TERMINAL_FONT_SIZE = 13;
export const TERMINAL_LINE_HEIGHT = 1.2;

/**
 * `tmux capture-pane -p -e` returns a screen snapshot whose rows are separated
 * by LF. LF alone advances a real terminal without returning to column zero,
 * so mirror playback must opt into xterm's LF -> CRLF conversion. The attach
 * channel is raw PTY data and deliberately keeps `convertEol: false`.
 */
export const MIRROR_TERMINAL_OPTIONS = {
  disableStdin: true,
  convertEol: true,
  fontFamily: TERMINAL_FONT_FAMILY,
  fontSize: TERMINAL_FONT_SIZE,
  lineHeight: TERMINAL_LINE_HEIGHT,
} satisfies ITerminalOptions;

export const ATTACH_TERMINAL_OPTIONS = {
  disableStdin: false,
  convertEol: false,
  fontFamily: TERMINAL_FONT_FAMILY,
  fontSize: TERMINAL_FONT_SIZE,
  lineHeight: TERMINAL_LINE_HEIGHT,
  cursorBlink: true,
} satisfies ITerminalOptions;
