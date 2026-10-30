import { ThemeSwitch } from "../appearance/theme-switch";

export function AppearanceSettings() {
  return <section className="settings-card appearance-settings">
    <header className="settings-card-head">
      <span>03</span>
      <div><h2>界面外观</h2><p>选择熟悉的像素风界面，或更柔和通透的液态玻璃界面。</p></div>
    </header>
    <div className="appearance-choice">
      <div><h3>界面版本</h3><p>切换立即生效，保留当前页面、筛选与任务。偏好仅保存在当前浏览器。</p></div>
      <ThemeSwitch />
    </div>
    <div className="appearance-previews" aria-label="两种界面风格说明">
      <article className="appearance-preview-pixel">
        <div className="appearance-swatch" aria-hidden="true"><i /><i /><i /></div>
        <h3>像素风</h3><p>纸张底色、细线分隔、紧凑排版。默认沿用这一版本。</p>
      </article>
      <article className="appearance-preview-glass">
        <div className="appearance-swatch" aria-hidden="true"><i /><i /><i /></div>
        <h3>液态玻璃</h3><p>柔和光影、通透材质、悬浮导航。覆盖行情、数据管理、监控、设置与登录。</p>
      </article>
    </div>
    <p className="appearance-note">表格与日志保持清晰底色；系统要求减少动态或透明效果时，界面会自动简化。</p>
  </section>;
}
