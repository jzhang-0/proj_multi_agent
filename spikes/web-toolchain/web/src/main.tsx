import { Terminal } from "@xterm/xterm";
import { useEffect, useRef } from "preact/hooks";
import { render } from "preact";

function App() {
  const terminalHost = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!terminalHost.current) return;
    const terminal = new Terminal({ convertEol: true, cursorBlink: false });
    terminal.open(terminalHost.current);
    terminal.writeln("xterm.js bundle loaded locally");
    terminal.writeln("Preact + TypeScript + Hatch wheel spike");
    return () => terminal.dispose();
  }, []);

  return (
    <section>
      <h1>Web toolchain spike</h1>
      <p data-testid="status">Preact component mounted; xterm.js is bundled locally.</p>
      <div id="terminal" ref={terminalHost} />
    </section>
  );
}

render(<App />, document.getElementById("app")!);
