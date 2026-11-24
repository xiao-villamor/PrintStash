/** The shipped Orca settings form preserves configuration through the host bridge.
 * Only Orca's external bridge is represented; HTML, controls and browser behavior are real.
 */
const { before, after, beforeEach, afterEach, describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { createRequire } = require('node:module');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const { chromium, expect } = createRequire(path.join(root, 'frontend/package.json'))('@playwright/test');
const html = execFileSync('python3', ['-c',
  'from tests.fakes.orca_host import plugin_host\nwith plugin_host() as host: print(host.module.config_ui())',
], { cwd: path.join(root, 'backend'), encoding: 'utf8' });
const connection = { enabled: true, url: 'https://printstash.example.test', username: 'test-user', api_key: 'psk_fake_key' };
let browser;
let page;
let errors;

async function mount(config = {}, context = {}) {
  await page.evaluate(({ config, context }) => {
    window.saved = [];
    window.restoreCount = 0;
    window.configHandlers = [];
    window.currentContext = { scope: 'global', readOnly: false, hasPresetOverride: false, ...context };
    window.orca = {
      getConfig: () => config,
      getContext: () => window.currentContext,
      saveConfig: value => window.saved.push(value),
      onConfig: cb => { window.configHandlers.push(cb); cb(config); },
      restoreDefaults: () => window.restoreCount++,
    };
    window.hostReply = (value, context) => {
      if (context) window.currentContext = context;
      window.configHandlers.forEach(cb => cb(value));
    };
  }, { config, context });
  await page.setContent(html);
}

before(async () => { browser = await chromium.launch(); });
after(async () => { await browser.close(); });
beforeEach(async () => {
  page = await browser.newPage({ viewport: { width: 900, height: 1000 } });
  errors = [];
  page.on('pageerror', error => errors.push(error.message));
});
afterEach(async () => { await page.close(); assert.deepEqual(errors, []); });

describe('Orca settings', () => {
  it('restores saved configuration', async () => {
    await mount({ ...connection, collection: 'Parts', revision_notes: 'Stronger', revision_status: 'known_good' });
    await expect(page.getByLabel('Server URL')).toHaveValue(connection.url);
    await expect(page.getByLabel('Username', { exact: true })).toHaveValue(connection.username);
    await expect(page.getByLabel('API key', { exact: true })).toHaveValue(connection.api_key);
    await expect(page.getByLabel('Collection (optional)')).toHaveValue('Parts');
    await expect(page.getByLabel('Revision notes (optional)')).toHaveValue('Stronger');
    await expect(page.getByLabel('Revision status')).toHaveValue('known_good');
  });

  it('rejects missing connection fields', async () => {
    await mount();
    await page.getByLabel('Upload exports to PrintStash').check();
    await page.getByRole('button', { name: 'Save settings' }).click();
    await expect(page.getByRole('status')).toHaveText('Enter the server URL, username and API key.');
    await expect(page.getByLabel('Server URL')).toBeFocused();
    assert.deepEqual(await page.evaluate(() => window.saved), []);
  });

  for (const url of ['file:///tmp/stash', 'https://user:secret@example.test', 'https://example.test?key=secret', 'https://example.test/#fragment', 'http://example.test:99999']) {
    it(`rejects unsupported connection URL ${url}`, async () => {
      await mount(connection);
      await page.getByLabel('Server URL').fill(url);
      await page.getByRole('button', { name: 'Save settings' }).click();
      await expect(page.getByRole('status')).toHaveText('Use an HTTP(S) server URL without credentials, query or fragment.');
      assert.deepEqual(await page.evaluate(() => window.saved), []);
    });
  }

  it('saves the complete configuration through Orca', async () => {
    await mount(connection);
    await page.getByLabel('Collection (optional)').fill('Parts/Brackets');
    await page.getByLabel('Require a single matching source model').check();
    await page.getByText('Revision defaults', { exact: true }).click();
    await page.getByLabel('Revision label (optional)').fill('Strong');
    await page.getByLabel('Revision notes (optional)').fill('Extra walls');
    await page.getByLabel('Revision status').selectOption('known_good');
    await page.getByLabel('Make this revision recommended').check();
    await page.getByRole('button', { name: 'Save settings' }).click();
    const expected = { ...connection, collection: 'Parts/Brackets', strict_mapping: true, revision_label: 'Strong', revision_notes: 'Extra walls', revision_status: 'known_good', recommended: true };
    assert.deepEqual(await page.evaluate(() => window.saved), [expected]);
    await page.evaluate(value => window.hostReply(value), expected);
    await expect(page.getByRole('status')).toHaveText('Settings saved.');
  });

  it('waits for host acknowledgement before claiming settings were saved', async () => {
    await mount(connection);
    await page.getByRole('button', { name: 'Save settings' }).click();
    await expect(page.getByRole('status')).toHaveText('Sent to OrcaSlicer for saving…');
  });

  it('saves disabled uploads without credentials', async () => {
    await mount();
    await page.getByRole('button', { name: 'Save settings' }).click();
    assert.deepEqual(await page.evaluate(() => window.saved), [{ enabled: false, url: null, username: null, api_key: null, collection: null, strict_mapping: false, revision_label: null, revision_notes: null, revision_status: 'needs_test', recommended: false }]);
  });

  it('identifies preset overrides', async () => {
    await mount(connection, { scope: 'preset', hasPresetOverride: true });
    await expect(page.getByText('This process preset.', { exact: true })).toBeVisible();
    await expect(page.getByText(/Configure your shared connection in Plugins/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Use global settings' })).toBeVisible();
  });

  it('restores global settings for a preset', async () => {
    await mount({ ...connection, collection: 'Override' }, { scope: 'preset', hasPresetOverride: true });
    await page.getByRole('button', { name: 'Use global settings' }).click();
    assert.equal(await page.evaluate(() => window.restoreCount), 1);
    await page.evaluate(value => window.hostReply(value, { scope: 'preset', hasPresetOverride: false, readOnly: false }), connection);
    await expect(page.getByLabel('Collection (optional)')).toHaveValue('');
    await expect(page.getByRole('status')).toHaveText('Using global settings.');
    await expect(page.getByRole('button', { name: 'Use global settings' })).toBeHidden();
  });

  it('respects read-only host configuration', async () => {
    await mount(connection, { readOnly: true });
    await expect(page.getByLabel('Server URL')).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Save settings' })).toBeDisabled();
    await expect(page.getByRole('status')).toHaveText('These settings are read-only.');
  });

  it('keeps the API key masked until explicitly shown', async () => {
    await mount(connection);
    const field = page.getByLabel('API key', { exact: true });
    await expect(field).toHaveAttribute('type', 'password');
    await page.getByRole('button', { name: 'Show API key' }).click();
    await expect(field).toHaveAttribute('type', 'text');
    await page.getByRole('button', { name: 'Hide API key' }).click();
    await expect(field).toHaveAttribute('type', 'password');
    await expect(field).toHaveValue(connection.api_key);
  });

  it('identifies unsaved edits', async () => {
    await mount(connection);
    await page.getByLabel('Username', { exact: true }).fill('updated-user');
    await expect(page.getByRole('status')).toHaveText('Unsaved changes.');
    await page.getByLabel('Username', { exact: true }).fill(connection.username);
    await expect(page.getByRole('status')).toHaveText('No unsaved changes.');
  });

  it('explains the profile snapshot boundary', async () => {
    await mount();
    await expect(page.getByText(/snapshots from each export; unused Orca presets are not synchronized/)).toBeVisible();
  });

  for (const width of [360, 900]) {
    it(`fits the ${width}px host window`, async () => {
      await page.setViewportSize({ width, height: 900 });
      await mount();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await expect(page.getByRole('button', { name: 'Save settings' })).toBeVisible();
    });
  }

  it('follows the host theme', async () => {
    await mount();
    await page.addStyleTag({ content: ':root { --orca-bg: #202020; --orca-fg: #eeeeee; --orca-border: #484848; --orca-muted: #aaaaaa; }' });
    await page.evaluate(() => document.documentElement.dataset.orcaTheme = 'dark');
    await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(32, 32, 32)');
    await expect(page.getByLabel('Server URL')).toHaveCSS('color', 'rgb(238, 238, 238)');
    await page.addStyleTag({ content: ':root { --orca-bg: #ffffff; --orca-fg: #202938; }' });
    await page.evaluate(() => document.documentElement.dataset.orcaTheme = 'light');
    await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  });
});
