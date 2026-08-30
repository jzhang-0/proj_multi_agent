# Web 前端工具链与终端组件调研（T-005）

## 结论

Web 目标是桌面浏览器中对齐 TUI 的全部能力，且与 TUI 并列保留。因此建议采用 **Preact + TypeScript + esbuild**：运行时足够轻，能提供组件化状态管理、事件处理和可测试的 DOM，同时不引入 React 兼容层的额外边界。TypeScript 只在构建期运行，Python 服务端 wheel 只携带编译后的静态资源。

终端面板建议使用当前 scoped 包 **`@xterm/xterm`**（本 spike 解析到 6.0.0），由 npm 在构建时下载并打进本地 `app.js`，同时复制 xterm CSS；浏览器运行时不访问 CDN，也不需要 Node。xterm 只负责浏览器中的终端显示和输入事件，tmux/PTY 的数据仍须由后端通过 WebSocket（或既定的实时通道）提供。

本结论在隔离目录 `spikes/web-toolchain/` 中有可复核原型，不修改现有 `src/` 产品代码。

## 1. Preact 与轻量原生组件的比较

| 方案 | 优点 | 成本/风险 | 对 TUI 全量对齐的判断 |
| --- | --- | --- | --- |
| Preact + TypeScript | 函数组件、hooks、JSX 和成熟的 DOM 测试工具；复杂面板之间共享状态、异步请求和局部更新较直接；可按页面/功能拆分并延迟加载 | 需要引入一个运行时依赖和 JSX 构建步骤；团队需约定状态边界和组件生命周期 | **推荐**。任务板、列表/详情、时间线、筛选表单、弹窗、实时状态和终端面板均有组件状态，手写 DOM 的维护成本会随对齐范围增长 |
| 原生 DOM / Custom Elements + TypeScript | 无 UI 运行时依赖，静态资源最小；浏览器原生 API 直接可用 | 需要自行维护渲染、状态订阅、事件解绑、焦点/键盘可访问性和组件间通信；异步状态和实时数据容易产生重复样板 | 可用于静态壳、少量控件或不采用框架的局部；不建议作为全 Web 控制台的默认方案 |

建议初版只使用 Preact 本身，不启用 `preact/compat`；只有确实需要复用 React 生态组件时再评估兼容层。终端单独封装为 `<TerminalPanel>`，把连接、重连、尺寸同步和输入发送放在组件边界外的服务层，避免把后端会话生命周期隐含在视图中。

### 依赖与版本策略

原型的直接依赖如下（`npm ls --depth=0` 的实际解析结果）：

| 包 | 用途 | 原型解析版本 |
| --- | --- | --- |
| `preact` | UI 组件与 JSX | 10.29.8 |
| `@xterm/xterm` | 终端渲染、键盘输入和 ANSI/Unicode 显示 | 6.0.0 |
| `esbuild` | TypeScript/TSX bundle（开发依赖） | 0.25.12 |
| `typescript` | 类型检查（开发依赖） | 5.9.3 |
| `@playwright/test` | 本机 E2E 验证（开发依赖） | 1.62.1 |

产品落地时应提交 lockfile，升级时同步审查许可证和 bundle 体积。`xterm-addon-fit`、WebGL、search、serialize 等 addon 不是本 spike 的必需依赖；若引入，须分别锁定版本并复核其许可证与浏览器兼容性。

## 2. 终端组件、许可证与无 CDN 打包

### 组件选择

`@xterm/xterm` 是 xterm.js 的当前 scoped npm 包，提供 TypeScript 声明且运行时无包依赖；核心适合在浏览器中显示 shell、vim、tmux 等终端程序的输出，并处理 Unicode/CJK/emoji/IME 等终端输入显示场景。它不是 PTY，也不会在浏览器内启动 bash 或 tmux：后端仍需连接现有 tmuxctl/PTY，并把字节流转成终端写入数据。

### 许可证

| 组件 | 许可证 | 证据/交付要求 |
| --- | --- | --- |
| Preact | MIT | 上游仓库声明 MIT；随产品发布保留依赖的版权/许可证通知 |
| `@xterm/xterm` | MIT | npm 元数据和上游 LICENSE 均为 MIT；发布包保留通知 |

本 spike 已从安装后的 `node_modules/@xterm/xterm/LICENSE` 和 `node_modules/preact/LICENSE` 检查到 MIT 文本。正式发布前，建议使用 lockfile 对全部生产依赖生成许可证清单；开发依赖（TypeScript、esbuild、Playwright）不应被打进运行时 bundle。

### 本地静态资源路径

原型流程为：

```text
npm ci
  -> npm run typecheck
  -> npm run build
  -> web/dist/index.html
                 web/dist/assets/app.js       (Preact + xterm bundle)
                 web/dist/assets/xterm.css
```

`app.js` 使用 esbuild bundle 为单个 ESM 文件，xterm CSS 从 npm 包复制到 `web/dist/assets/`；`index.html` 仅引用相对路径。服务端或 wheel 运行时不需要 Node/npm，浏览器也没有任何 CDN 请求。生产应用可在终端页面首次打开时再动态加载终端 bundle，以降低非终端页面的首屏成本。

原型 bundle 的实际大小是 `app.js` 357,576 bytes、`xterm.css` 7,112 bytes、`index.html` 368 bytes（未压缩传输体积；尚未做 gzip/Brotli 测量）。这使“无 CDN”可行，但终端 bundle 应在产品性能验收中单独评估缓存、压缩和按需加载。

## 3. TypeScript 产物进入 Hatch wheel

原型的 `spikes/web-toolchain/pyproject.toml` 使用 Hatch 的 `force-include`：

```toml
[tool.hatch.build.targets.wheel]
packages = ["spike_pkg"]

[tool.hatch.build.targets.wheel.force-include]
"web/dist" = "spike_pkg/static"
```

这会把构建目录递归映射到 Python 包内的 `spike_pkg/static/`。产品集成时应把右侧目标换成实际服务端包的静态目录，并让服务端从包资源读取文件（例如 `importlib.resources`），不要依赖源码工作目录或运行时 Node。

实际验证（工作目录：`spikes/web-toolchain`）：

```text
uv build --wheel --out-dir dist-wheel-final
Successfully built dist-wheel-final/amux_web_toolchain_spike-0.0.1-py3-none-any.whl

unzip -l ...whl
  spike_pkg/static/index.html       368 bytes
  spike_pkg/static/assets/app.js    357576 bytes
  spike_pkg/static/assets/xterm.css 7112 bytes

uv run --python 3.11 --isolated --no-project \
  --with dist-wheel-final/amux_web_toolchain_spike-0.0.1-py3-none-any.whl \
  python -c '...importlib.resources check...'
wheel static resources: PASS
```

因此“TS 构建产物随 Hatch wheel 分发”已在最小 hello 页面上验证。`web/dist/` 和 wheel 属于构建产物，应在源码仓库中忽略；`package-lock.json` 应提交，以便构建可重复。

## 4. Playwright 本机验证

原型的 `playwright.config.ts` 使用 `@playwright/test`，启动本地 Node 静态服务器（`127.0.0.1:4173`），并在测试前执行 `npm run build`。测试文件 `tests/hello.spec.ts` 验证：

1. Preact 页面标题和状态文字可见；
2. `/assets/app.js` 可以从本地服务器取得，并包含 xterm bundle；
3. xterm 的 `.xterm-screen` 和 `.xterm` 容器已经挂载。

实际命令和结果：

```text
npm run typecheck && npm run test:e2e

Running 1 test using 1 worker
✓ 1 tests/hello.spec.ts:3:1 › bundled Preact page and xterm mount locally (306ms)
1 passed (1.9s)
```

Playwright 浏览器二进制是开发/CI 环境依赖，不应放进 Python wheel。新环境按 Playwright 版本执行 `npx playwright install chromium`（或 CI 的等价缓存步骤）即可；运行时服务只需提供静态页面。

## 5. 对 Web 全量对齐的边界提示

- 本 spike 只验证“本地 bundle + Preact mount + xterm mount + wheel 收纳 + 浏览器测试”，没有连接实际 gateway、bus、roster、team 或 tmuxctl。
- xterm 的高难度项是 tmux 窗格实时画面：需要后端会话/PTY 的生命周期、WebSocket 传输、断线重连、尺寸同步、输出节流和权限边界；不能把静态 xterm mount 视为此项已完成。
- Web 路线仍应复用已有控制面和 tmuxctl 能力；前端 API 层应保持与 TUI 同一事实来源，终端视图是额外的实时数据通道。
- 首版应预留错误、断线和无权限状态的组件状态；否则看似完成的列表/操作在真实总线延迟下会与 TUI 行为不一致。

## 6. 原型文件与复现入口

```text
spikes/web-toolchain/
├── package.json
├── package-lock.json
├── tsconfig.json
├── pyproject.toml
├── web/index.html
├── web/src/main.tsx
├── scripts/build.mjs
├── scripts/serve.mjs
├── playwright.config.ts
└── tests/hello.spec.ts
```

在该目录执行：

```bash
npm ci
npm run typecheck
npm run build
npm run test:e2e
uv build --wheel --out-dir dist-wheel-final
```

## 参考资料

- [Preact 官方仓库（MIT、TypeScript/轻量定位）](https://github.com/preactjs/preact)
- [Preact TypeScript 指南](https://preactjs.com/guide/v10/typescript/)
- [xterm.js 官方仓库（能力与 MIT 许可证）](https://github.com/xtermjs/xterm.js/)
- [`@xterm/xterm` npm 包](https://www.npmjs.com/package/%40xterm/xterm)
- [xterm.js LICENSE](https://github.com/xtermjs/xterm.js/blob/master/LICENSE)
- [Hatch build 配置：force-include](https://hatch.pypa.io/dev/config/build/)
- [Playwright 浏览器安装](https://playwright.dev/docs/browsers)
- [Playwright 入门与本地/CI 测试](https://playwright.dev/docs/intro)
