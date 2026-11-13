/* Both registration orders must keep the original elements and storage separate. */
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../custom_components/improved_shopping_list_dev/frontend');
const originalNames = [
  'improved-shopping-list-panel', 'isl-alcampo-settings', 'isl-analytics-insights',
  'isl-analytics', 'isl-carrefour-account', 'isl-catalog', 'isl-catalog-sources',
  'isl-mercadona-catalog', 'isl-eci-catalog', 'isl-categories', 'isl-dia-account',
  'isl-dia-prices-settings', 'isl-icon-picker', 'isl-lidl-account', 'isl-logs',
  'isl-meal-consumption', 'isl-meal-routines', 'isl-notification-settings',
  'isl-pending-information-refresh', 'isl-purchases', 'isl-readers', 'isl-recipe-editor',
  'isl-settings', 'isl-shopping-places', 'isl-zone-map',
  ...['masymas', 'bm', 'consum', 'lasirena', 'eroski'].map(store => `isl-${store}-catalog`),
];

async function main() {
  const modules = (await fs.readdir(root)).filter(name => name.endsWith('.js'));
  const version = require('../package.json').version;
  const html = `<!doctype html><script type="module">await Promise.all(${JSON.stringify(modules)}.map(name=>import('/'+name+'?v=${version}')));window.ready=true;</script>`;
  const server = http.createServer(async (req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    if (pathname === '/') { res.end(html); return; }
    const file = path.resolve(root, '.' + pathname);
    if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    try {
      const body = await fs.readFile(file);
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/plain');
      res.end(body);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome'});
    for (const originalFirst of [true, false]) {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', error => {errors.push(error.message); console.error(error.message);});
      await page.addInitScript(({names, first}) => {
        window.registerOriginal = () => {
          window.originalConstructors = new Map();
          for (const name of names) {
            const element = class extends HTMLElement {};
            customElements.define(name, element);
            window.originalConstructors.set(name, element);
          }
        };
        if (first) window.registerOriginal();
        localStorage.setItem('isl-list-layout', 'rows');
        localStorage.setItem('isl-stock-grouped', 'true');
      }, {names: originalNames, first: originalFirst});
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      await page.waitForFunction(() => window.ready);
      const result = await page.evaluate(first => {
        if (!first) window.registerOriginal();
        const panel = document.createElement('improved-shopping-list-dev-panel');
        const distinct = [...window.originalConstructors].every(([name, ctor]) => {
          const devName = name === 'improved-shopping-list-panel'
            ? 'improved-shopping-list-dev-panel' : name.replace('isl-', 'isl-dev-');
          return customElements.get(name) === ctor && Boolean(customElements.get(devName))
            && customElements.get(devName) !== ctor;
        });
        localStorage.setItem('isl-dev-list-layout', 'grid');
        return {distinct, layout: panel._listLayout, grouped: panel._stockGrouped,
          originalLayout: localStorage.getItem('isl-list-layout')};
      }, originalFirst);
      assert.deepEqual(result, {distinct: true, layout: 'grid', grouped: false, originalLayout: 'rows'});
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Dev coexistence passed in both load orders; original elements and preferences preserved.');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
