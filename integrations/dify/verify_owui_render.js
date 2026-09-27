/**
 * Open WebUI 图片渲染真实验证
 * =============================
 *
 * 调研报告里"Open WebUI 能渲染 ![]() 但不能渲染 <img>"这个结论，
 * 之前只是**源码级推断**。这里用无头浏览器实际跑一遍，看 DOM 里到底有没有 <img>，
 * 以及图片是否真的加载成功（naturalWidth > 0）。
 *
 * 用法：
 *   OWUI_TOKEN=xxx node verify_owui_render.js
 */
const { chromium } = require('playwright');

const BASE = process.env.OWUI_URL || 'http://localhost:3000';
const TOKEN = process.env.OWUI_TOKEN || '';
const MODEL = process.env.OWUI_MODEL || 'mmkb_multimodal_kb';
const QUERY = process.env.QUERY || '接地保护示意图说明了什么？';

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  const failed = [];
  page.on('requestfailed', r => failed.push(`${r.url()} :: ${r.failure()?.errorText}`));
  const imgResponses = [];
  page.on('response', async r => {
    if (r.request().resourceType() === 'image') {
      imgResponses.push({ url: r.url(), status: r.status() });
    }
  });

  // 直接注入 token，跳过登录 UI
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.evaluate(t => localStorage.setItem('token', t), TOKEN);

  // 走 New Chat 深链
  await page.goto(`${BASE}/?models=${MODEL}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(3000);

  // 选模型（若未自动选中）
  try {
    const sel = page.locator('#model-selector-0-button, button:has-text("多模态知识库")').first();
    if (await sel.count()) {
      await sel.click();
      await page.waitForTimeout(800);
      const opt = page.locator(`text=${MODEL}`).first();
      if (await opt.count()) await opt.click();
      await page.waitForTimeout(800);
    }
  } catch (e) { /* 忽略，可能已选中 */ }

  // 输入并发送
  const box = page.locator('#chat-input, textarea#chat-input, textarea').first();
  await box.waitFor({ timeout: 30000 });
  await box.click();
  await box.fill(QUERY);
  await page.waitForTimeout(800);
  await box.press('Enter');
  await page.waitForTimeout(1500);

  // 若 Enter 没发出去，点发送按钮（截图显示过这种情况）
  const stillThere = await box.inputValue().catch(() => '');
  if (stillThere.trim()) {
    const send = page.locator('button[type="submit"], button[aria-label*="Send"], button:has(svg):near(textarea)').last();
    if (await send.count()) { await send.click().catch(() => {}); }
    await page.waitForTimeout(1500);
  }

  // 等回答渲染完（出现 Markdown 图片或超时）
  let imgs = [];
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(1500);
    imgs = await page.$$eval('img', els => els.map(e => ({
      src: e.currentSrc || e.src,
      w: e.naturalWidth, h: e.naturalHeight,
      visible: e.offsetParent !== null,
    })).filter(x => x.src && x.src.includes('/images/')));
    if (imgs.length) break;
  }

  const text = await page.innerText('body').catch(() => '');
  const htmlImgCount = await page.$$eval('img', els => els.length);

  console.log('='.repeat(68));
  console.log('Open WebUI 图片渲染验证');
  console.log('='.repeat(68));
  console.log(`页面总 <img> 元素: ${htmlImgCount}`);
  console.log(`指向我们检索服务的 <img>: ${imgs.length}`);
  imgs.forEach((x, i) => {
    console.log(`  [${i + 1}] 加载成功=${x.w > 0}  尺寸=${x.w}x${x.h}  可见=${x.visible}`);
    console.log(`      ${x.src}`);
  });
  console.log(`\n回答里出现 '![' 字面量（说明没渲染）: ${text.includes('![')}`);
  console.log(`回答里出现图片文件名（说明渲染了）: ${text.includes('.jpg') && !text.includes('![')}`);
  if (failed.length) {
    console.log('\n请求失败:');
    failed.slice(0, 5).forEach(f => console.log('  ' + f));
  }
  const img404 = imgResponses.filter(r => r.status >= 400);
  if (img404.length) {
    console.log('\n图片 HTTP 错误:');
    img404.slice(0, 5).forEach(r => console.log(`  ${r.status} ${r.url}`));
  }

  await page.screenshot({ path: 'owui_render.png', fullPage: true });
  console.log('\n截图已保存: _verify/owui_render.png');

  await browser.close();
  const ok = imgs.length > 0 && imgs.every(x => x.w > 0);
  console.log(`\n结论: ${ok ? '✅ 图片确实渲染并加载成功' : '❌ 图片未渲染或加载失败'}`);
  process.exit(ok ? 0 : 1);
})();
