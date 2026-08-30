import { render } from "preact";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../../src/app";

afterEach(() => {
  document.body.innerHTML = "";
});

describe("App shell", () => {
  it("renders the local console shell and status", () => {
    const root = document.createElement("div");
    document.body.append(root);
    render(<App />, root);

    expect(root.querySelector("h1")?.textContent).toBe("控制台骨架");
    expect(root.querySelector('[data-testid="status"]')?.textContent).toContain("本地静态资源");
  });
});
