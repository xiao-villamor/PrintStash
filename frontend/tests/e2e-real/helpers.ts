import {
  test as base,
  expect,
  request as pwRequest,
  type Browser,
  type Page,
} from "@playwright/test";

// Talks straight to the real backend to seed the first admin (once) and mint a
// real JWT, then installs it as the same HttpOnly cookie used by the app.
// Order-independent: this runs in the test phase,
// after Playwright has confirmed both web servers are up.
const apiPort = Number(process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410);
const API = `http://127.0.0.1:${apiPort}`;

export const ADMIN = { username: "admin", password: "admin1234" };
const SETUP_TOKEN = "playwright-setup-token-123";

let cachedToken: string | null = null;
let cachedUser: string | null = null;

async function ensureAuth(): Promise<{ token: string; user: string }> {
  if (cachedToken && cachedUser) return { token: cachedToken, user: cachedUser };

  const ctx = await pwRequest.newContext();
  try {
    const status = await (await ctx.get(`${API}/api/v1/setup/status`)).json();
    if (!status.configured) {
      const res = await ctx.post(`${API}/api/v1/setup`, {
        data: { ...ADMIN, setup_token: SETUP_TOKEN, storage_backend: "local" },
      });
      if (!res.ok() && res.status() !== 409) {
        throw new Error(`setup failed: ${res.status()} ${await res.text()}`);
      }
    }
    const login = await ctx.post(`${API}/api/v1/auth/login`, { data: ADMIN });
    if (!login.ok()) throw new Error(`login failed: ${login.status()} ${await login.text()}`);
    cachedToken = (await login.json()).access_token;

    const me = await ctx.get(`${API}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${cachedToken}` },
    });
    cachedUser = JSON.stringify(await me.json());
  } finally {
    await ctx.dispose();
  }
  return { token: cachedToken!, user: cachedUser! };
}

// `page` override seeds the real HttpOnly cookie before any navigation.
/* eslint-disable react-hooks/rules-of-hooks -- `use` here is Playwright's fixture callback, not a React hook. */
export const test = base.extend({
  page: async ({ page }, use) => {
    const { token, user } = await ensureAuth();
    await page
      .context()
      .addCookies([
        { name: "printstash_session", value: token, url: API, httpOnly: true, sameSite: "Strict" },
      ]);
    await page.addInitScript((u) => {
      localStorage.setItem("printstash.user", u);
    }, user);
    await use(page);
  },
});
/* eslint-enable react-hooks/rules-of-hooks */

// Log in as any user against the real backend and return what the app stores in
// as its HttpOnly cookie + non-secret user metadata.
export async function authBundleFor(
  username: string,
  password: string,
): Promise<{ token: string; user: string }> {
  const ctx = await pwRequest.newContext();
  try {
    const login = await ctx.post(`${API}/api/v1/auth/login`, { data: { username, password } });
    if (!login.ok()) throw new Error(`login failed for ${username}: ${login.status()}`);
    const token = (await login.json()).access_token;
    const me = await ctx.get(`${API}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    return { token, user: JSON.stringify(await me.json()) };
  } finally {
    await ctx.dispose();
  }
}

// A fresh, isolated browser context already authenticated as `bundle`.
// Caller is responsible for closing the returned context.
export async function authedContext(
  browser: Browser,
  bundle: { token: string; user: string },
): Promise<{ context: Awaited<ReturnType<Browser["newContext"]>>; page: Page }> {
  const context = await browser.newContext();
  await context.addCookies([
    {
      name: "printstash_session",
      value: bundle.token,
      url: API,
      httpOnly: true,
      sameSite: "Strict",
    },
  ]);
  const page = await context.newPage();
  await page.addInitScript((u) => {
    localStorage.setItem("printstash.user", u);
  }, bundle.user);
  return { context, page };
}

export { expect };
// Specs import `test`, `expect` and `Page` from here rather than from
// `@playwright/test`, so this module has to re-export the type too.
export type { Page };
