/** Dify webapp 图片渲染验证 */
const { chromium } = require('playwright');
const URL = process.env.DIFY_URL;
const QUERY = process.env.QUERY || '接地保护示意图说明了什么？';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const failed = [];
  page.on('requestfailed', r => failed.push(`${r.url()} :: ${r.failure()?.errorText}`));
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(4000);

  const box = page.locator('textarea').first();
  await box.waitFor({ timeout: 30000 });
  await box.click(); await box.fill(QUERY);
  await page.waitForTimeout(500);
  await box.press('Enter');
  await page.waitForTimeout(2000);
  if ((await box.inputValue().catch(()=>'')).trim()) {
    const btn = page.locator('button[type="submit"], button:has(svg)').last();
    if (await btn.count()) await btn.click().catch(()=>{});
  }
  // 等流式结束：Dify 在生成中会显示「停止响应」按钮
  for (let i = 0; i < 60; i++) {
    await page.waitForTimeout(1000);
    const stopping = await page.locator('text=停止响应').count().catch(() => 0);
    if (i > 4 && stopping === 0) break;
  }
  await page.waitForTimeout(3000);

  let imgs = [];
  for (let i = 0; i < 15; i++) {
    await page.waitForTimeout(2000);
    imgs = await page.$$eval('img', els => els.map(e => ({
      src: e.currentSrc || e.src, w: e.naturalWidth, h: e.naturalHeight,
      visible: e.offsetParent !== null })).filter(x => x.src && x.src.includes('/images/')));
    if (imgs.length) break;
  }
  const text = await page.innerText('body').catch(()=> '');
  console.log('='.repeat(66));
  console.log('Dify WebApp 图片渲染验证');
  console.log('='.repeat(66));
  console.log(`指向检索服务的 <img>: ${imgs.length}`);
  imgs.forEach((x,i)=>console.log(`  [${i+1}] 加载成功=${x.w>0} 尺寸=${x.w}x${x.h} 可见=${x.visible}\n      ${x.src}`));
  console.log(`\n回答里出现 '![' 字面量（= Markdown 没被渲染）: ${text.includes('![')}`);
  console.log(`回答里出现 '[图片]' 字面量: ${text.includes('[图片]')}`);
  const md = (text.match(/!\[[^\]]*\]\(/g) || []).length;
  console.log(`页面上未渲染的 Markdown 图片标记数: ${md}`);
  if (failed.length) { console.log('\n请求失败(前5):'); failed.slice(0,5).forEach(f=>console.log('  '+f)); }
  await page.screenshot({ path: 'dify_render.png', fullPage: true });
  console.log('\n截图: /tmp/dify_verify/dify_render.png');
  await browser.close();
})();
