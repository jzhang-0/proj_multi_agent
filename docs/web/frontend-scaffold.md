# Web 正式前端工程骨架方案（T-010）

## 结论与边界

正式 Web 前端沿用 T-005 已验证的 **Preact + TypeScript + esbuild**。Node/npm 只在构建与测试阶段存在；FastAPI/ASGI 运行时从 Python 包内读取编译后的资源，浏览器不访问 CDN，也不需要 Node。

本方案只定义工程布局、构建入口、资源映射和 CI 顺序，不创建 `web/` 或修改 `src/`。实现 WEB-005 时以本文件为骨架；WEB-003 合入后，Python 端的实际包名和静态资源根固定为 `web` / `web/static`。

## 1. 目录布局

正式前端应位于仓库根的 `web/`，与 `src/` 的 Python 代码分离：

```text
web/
├── package.json              # scripts、生产/开发依赖
├── package-lock.json         # npm lockfile，必须提交
├── tsconfig.json             # strict TypeScript + Preact JSX
├── esbuild.mjs               # 或 scripts/build.mjs，唯一生产 bundle 入口
├── scripts/
│   ├── build.mjs             # 清理并生成 dist
│   ├── serve.mjs             # 本机 Playwright 静态服务器
│   └── licenses.mjs          # 生成生产依赖许可证清单
├── src/
│   ├── main.tsx              # bootstrap、路由和根节点
│   ├── app.tsx               # 页面壳、主题和全局错误状态
│   ├── api/                  # 与 docs/web/api-protocol.md 对齐的 fetch/WS 客户端
│   ├── components/           # Header、任务、成员、时间线等可复用组件
│   ├── views/                # workspace、task、help 等页面视图
│   ├── state/                # snapshot、revision、导航和筛选状态
│   └── styles/               # 本地 CSS，不内联远程资源
├── tests/
│   ├── unit/                 # Preact 组件/状态测试
│   └── e2e/                  # Playwright 本机浏览器测试
├── dist/                     # 构建产物，忽略，不手工编辑
└── THIRD_PARTY_LICENSES.json # 由锁定依赖生成、随源码审查
```

`dist/` 是唯一被打入 Python 包的前端输入目录。`src/` 在这里是 TypeScript 源码，与 Python 的 `src/` 不冲突，因为前端目录在仓库根的 `web/` 下。

组件按领域拆分，不在组件内复制 bus/team/work/roster 的状态模型；`api/` 只负责协议编码/解码，snapshot 的 `epoch/revision`、断线重连和能力开关按 [API 与实时协议设计稿](api-protocol.md) 实现。后端仍是唯一事实来源。

## 2. npm scripts 与依赖约定

正式 `web/package.json` 应至少提供以下脚本（命令名是 CI 的稳定接口）：

```json
{
  "private": true,
  "type": "module",
  "scripts": {
    "typecheck": "tsc --noEmit",
    "build": "node scripts/build.mjs",
    "serve": "node scripts/serve.mjs",
    "test:unit": "vitest run",
    "test:e2e": "playwright test",
    "licenses": "node scripts/licenses.mjs",
    "verify": "npm run typecheck && npm run test:unit && npm run licenses"
  }
}
```

生产依赖保持最小：`preact` 以及终端功能落地时使用的 `@xterm/xterm`。`typescript`、`esbuild`、`vitest`、jsdom/组件测试适配器、`@playwright/test` 和许可证生成脚本依赖均放在 `devDependencies`，不会进入浏览器 bundle。T-005 的 spike 已证明 Preact/xterm 可以由 esbuild 打成相对路径的本地静态资源；终端 bundle 可在 WEB-007 按需加载。

每次变更依赖时使用 npm 更新 lockfile，并提交 `web/package-lock.json`：

```bash
npm install --prefix web
npm ci --prefix web
```

CI 和发布机只使用 `npm ci --prefix web`，不使用无 lockfile 的 `npm install`。`package-lock.json` 的 `lockfileVersion`、registry 来源和 resolved/integrity 字段进入代码审查；构建不允许隐式从 CDN 下载浏览器资源。

## 3. bundle 与 Hatch wheel 的实际路径

WEB-003 的 Python 包目录为 `src/web/`，安装后的导入包名为 `web`，静态资源运行时路径为 `web/static/`。仓库源文件和 wheel 路径的关系如下：

| 阶段 | 路径 | 说明 |
| --- | --- | --- |
| TypeScript 输出 | `web/dist/index.html`、`web/dist/assets/*` | esbuild 和资源复制脚本的输入 |
| wheel force-include 源 | `web/dist` | 不依赖源码 `src/web/static` 是否存在构建前文件 |
| wheel 内目标 | `web/static` | 实际 Python 包资源根；`importlib.resources.files("web") / "static"` 可读 |
| Web 运行时 | package resource `web/static` | 不读取仓库 cwd，不调用 Node，不拼接绝对源码路径 |

WEB-003 合入并确认 `src/web` 成为 wheel 包后，在根 `pyproject.toml` 增加目标映射：

```toml
[tool.hatch.build.targets.wheel.force-include]
"web/dist" = "web/static"

[tool.hatch.build.targets.sdist.force-include]
"web/dist" = "src/web/static"
```

第一条是正式 wheel 的关键映射：左侧是 checkout 中的前端构建目录，右侧是 wheel archive 内的包路径。第二条让 sdist 也携带构建后的静态资源，使从 sdist 再构建 wheel 时不依赖 Node；若发布策略选择只从 checkout 构建，也必须在 `qa.release` 中明确禁止缺少 `web/dist` 的构建。

Python 端使用 `importlib.resources`，不要用 `Path.cwd()` 或项目源码相对路径：

```python
from importlib.resources import files

STATIC_ROOT = files("web").joinpath("static")
```

实现时要在 wheel 检查中断言 `web/static/index.html`、`web/static/assets/app.js` 和 CSS/终端资源均存在；`qa.release` 的源码外安装 smoke 还要从安装包读取根页面，证明运行时不需要 Node、源码路径或 CDN。`web/dist`、`web/node_modules`、Playwright report 和测试截图缓存都应被 `.gitignore` 忽略，只有 `package-lock.json` 与许可证清单提交。

## 4. CI 与发布构建顺序

前端必须在任何会构建 wheel/sdist 的步骤之前完成。推荐将 release workflow 的 build job 调整为以下顺序：

```text
checkout
  → setup Node（固定项目要求的 LTS major）
  → npm ci --prefix web
  → npm run typecheck --prefix web
  → npm run licenses --prefix web
  → npm run build --prefix web
  → npm run test:unit --prefix web
  → uv sync --locked
  → uv run ruff check .
  → uv run pytest -q
  → uv run python -m qa.release --out-dir dist
  → upload wheel/sdist
```

发布 workflow 当前的 Python 主线是 `uv sync --locked → ruff → pytest → qa.release`；前端四步应插在 `uv sync` 之前或至少 `qa.release` 之前。Node 构建失败、typecheck 失败、组件测试失败或许可证清单发生未审查变化都必须阻止制品上传。TestPyPI/PyPI 发布 job 只下载 build job 已检查的制品，不重新运行 npm 或重新生成前端资源。

本地等价入口：

```bash
npm ci --prefix web
npm run typecheck --prefix web
npm run licenses --prefix web
npm run build --prefix web
npm run test:unit --prefix web
npm run test:e2e --prefix web
uv run ruff check .
uv run pytest tests/test_release_package.py -q
uv run python -m qa.release --out-dir /tmp/amux-release
```

`qa.release` 要求在源码仓库外安装 wheel 并请求根页面；Playwright 的 Chromium 二进制是 CI/开发环境依赖，不进入 Python wheel。需要浏览器的 job 应按锁定的 `@playwright/test` 版本显式执行 `npx playwright install --with-deps chromium`（macOS 本机则执行对应的 `npx playwright install chromium`）。

## 5. lockfile 与许可证清单

### lockfile

- `web/package-lock.json` 是 npm 依赖的唯一锁定输入，必须和 `package.json` 同一提交更新。
- CI 使用 `npm ci --prefix web`；若 lockfile 与 manifest 不一致，npm 应直接失败。
- 升级依赖时记录生产依赖变化、bundle 体积变化和许可证变化；不要提交 `node_modules`。
- 许可证清单只枚举生产依赖，开发工具（TypeScript、esbuild、Vitest、Playwright）保留在审查输入中但不进入运行时资源。

### 许可证生成

`scripts/licenses.mjs` 读取 `web/node_modules` 中生产依赖的 `package.json` 与许可证文件，按包名、版本、license、版权声明和许可证文本输出稳定排序的 `THIRD_PARTY_LICENSES.json`。它应在 `npm ci` 后、bundle 前运行；构建脚本将该 JSON 一并复制到 `web/dist`，使后续 wheel 映射携带完整通知，而不是留下开发机 `node_modules` 路径：

```bash
npm run licenses --prefix web
npm run build --prefix web
git diff --check -- web/THIRD_PARTY_LICENSES.json
git diff --exit-code -- web/THIRD_PARTY_LICENSES.json
```

若正式实现选择 `license-checker` 等现成生成器，必须把生成器本身固定在 `devDependencies` 并锁进 `package-lock.json`；禁止在 CI 用未锁定的 `npx --yes` 下载生成器。清单中至少应出现 `preact` 和 `@xterm/xterm`，并保留 MIT 文本或对应文件引用。引入 xterm addon、组件测试库或其他生产依赖时必须自动进入同一清单。

浏览器分发包应在静态资源目录旁提供 MIT 第三方通知（例如由后端提供 `/static/THIRD_PARTY_LICENSES.json`），但许可证清单不应被 bundle 的业务代码动态执行。xterm 核心和 Preact 的许可证结论及无 CDN 验证见 [前端工具链与终端组件调研](frontend-toolchain.md)。

## 6. 骨架交接与验收边界

WEB-005 实现阶段的最小交接检查：

1. 在 `web/` 执行 `npm ci` 后，typecheck、unit、Playwright 和许可证脚本均可运行；
2. `npm run build` 只生成 `web/dist`，HTML 只引用相对本地资源；
3. Hatch force-include 将资源放进 wheel 的 `web/static`，而不是 spike 的 `spike_pkg/static` 或临时源码目录；
4. 源码外安装的 wheel 可由 `importlib.resources` 找到静态根页面，服务启动不依赖 Node；
5. release CI 先完成 Node 构建再构建 Python 制品，并用 lockfile 与许可证清单阻止漂移；
6. WEB-005 的页面、导航、主题和 snapshot 组件测试仍以 Web Goal 的完整枚举为准；本骨架方案不把“bundle 能构建”当作只读 SPA 已完成。

本方案不改变 TUI、gateway 手机群聊页或 Python `src/`；WEB-005～008 仍需逐项实现并通过 Playwright 视觉自验证，WEB-009 再复核 wheel/sdist 的最终内容。

## 参考

- [前端工具链与终端组件调研（T-005）](frontend-toolchain.md)
- [Web API 与实时协议设计稿](api-protocol.md)
- [Web 版架构方案比较](architecture-options.md)
- [Web 控制台 Goal 卷](../goals/web.md)
- [打包与发布](../releasing.md)
- [Hatch build：force-include](https://hatch.pypa.io/dev/config/build/)
