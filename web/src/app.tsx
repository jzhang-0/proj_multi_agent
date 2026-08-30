export function App() {
  return (
    <main class="app-shell">
      <header class="app-header">
        <div>
          <p class="eyebrow">AMUX WEB</p>
          <h1>控制台骨架</h1>
        </div>
        <span class="status" data-testid="status">
          本地静态资源已加载
        </span>
      </header>
      <section class="empty-state" aria-labelledby="next-step">
        <p class="empty-state__mark" aria-hidden="true">◎</p>
        <h2 id="next-step">等待工作区数据</h2>
        <p>WEB-005 将在这里接入 snapshot、任务、成员和工作对话视图。</p>
      </section>
    </main>
  );
}
