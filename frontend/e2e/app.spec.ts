import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "SecurePass123";
const PDF_BASE64 =
  "JVBERi0xLjMKJZOMi54gUmVwb3J0TGFiIEdlbmVyYXRlZCBQREYgZG9jdW1lbnQgKG9wZW5zb3VyY2UpCjEgMCBvYmoKPDwKL0YxIDIgMCBSCj4+CmVuZG9iagoyIDAgb2JqCjw8Ci9CYXNlRm9udCAvSGVsdmV0aWNhIC9FbmNvZGluZyAvV2luQW5zaUVuY29kaW5nIC9OYW1lIC9GMSAvU3VidHlwZSAvVHlwZTEgL1R5cGUgL0ZvbnQKPj4KZW5kb2JqCjMgMCBvYmoKPDwKL0NvbnRlbnRzIDcgMCBSIC9NZWRpYUJveCBbIDAgMCA2MTIgNzkyIF0gL1BhcmVudCA2IDAgUiAvUmVzb3VyY2VzIDw8Ci9Gb250IDEgMCBSIC9Qcm9jU2V0IFsgL1BERiAvVGV4dCAvSW1hZ2VCIC9JbWFnZUMgL0ltYWdlSSBdCj4+IC9Sb3RhdGUgMCAvVHJhbnMgPDwKCj4+IAogIC9UeXBlIC9QYWdlCj4+CmVuZG9iago0IDAgb2JqCjw8Ci9QYWdlTW9kZSAvVXNlTm9uZSAvUGFnZXMgNiAwIFIgL1R5cGUgL0NhdGFsb2cKPj4KZW5kb2JqCjUgMCBvYmoKPDwKL0F1dGhvciAoYW5vbnltb3VzKSAvQ3JlYXRpb25EYXRlIChEOjIwMjYxMTE2MDAyODI2KzAzJzAwJykgL0NyZWF0b3IgKGFub255bW91cykgL0tleXdvcmRzICgpIC9Nb2REYXRlIChEOjIwMjYxMTE2MDAyODI2KzAzJzAwJykgL1Byb2R1Y2VyIChSZXBvcnRMYWIgUERGIExpYnJhcnkgLSBcKG9wZW5zb3VyY2VcKSkgCiAgL1N1YmplY3QgKHVuc3BlY2lmaWVkKSAvVGl0bGUgKHVudGl0bGVkKSAvVHJhcHBlZCAvRmFsc2UKPj4KZW5kb2JqCjYgMCBvYmoKPDwKL0NvdW50IDEgL0tpZHMgWyAzIDAgUiBdIC9UeXBlIC9QYWdlcwo+PgplbmRvYmoKNyAwIG9iago8PAovRmlsdGVyIFsgL0FTQ0lJODVEZWNvZGUgL0ZsYXRlRGVjb2RlIF0gL0xlbmd0aCAyMjIKPj4Kc3RyZWFtCkdhcHFBWiM3RUQmOzkkITpbc2Q1X1hUV1dlMCUlRyZMbVNZVmRrUShsLUBsOiMwQ2VFJHJOSkQnXTcrPTNdYi5MVkA1dVJyPlopK0VbMHIpKkhMKmFidStJOkAsSjxaNCFfUk9HZmwpVT9mZ0YzWkxYaTgrLVRfOGhrbGpNR2ZzYCllcHNnXzhpM0JDajI1UUVFL2ROUEFsSFxTQU0hUmk5WmY2KFtUaGV0SCIxb1BZMD0pNzhrZD9VPTZhTyhTYGlhOVJGVGFeQVFDJE9gSj1LbThLKyQmIWElcGZ+PmVuZHN0cmVhbQplbmRvYmoKeHJlZgowIDgKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDYxIDAwMDAwIG4gCjAwMDAwMDAwOTIgMDAwMDAgbiAKMDAwMDAwMDE5OSAwMDAwMCBuIAowMDAwMDAwMzkyIDAwMDAwIG4gCjAwMDAwMDA0NjAgMDAwMDAgbiAKMDAwMDAwMDcyMSAwMDAwMCBuIAowMDAwMDAwNzgwIDAwMDAwIG4gCnRyYWlsZXIKPDwKL0lEIApbPGVjZDFjYWZkNzBkOGMzNzA0ZGQ3ZTkwZGE0YmMxZGU2PjxlY2QxY2FmZDcwZDhjMzcwNGRkN2U5MGRhNGJjMWRlNj5dCiUgUmVwb3J0TGFiIGdlbmVyYXRlZCBQREYgZG9jdW1lbnQgLS0gZGlnZXN0IChvcGVuc291cmNlKQoKL0luZm8gNSAwIFIKL1Jvb3QgNCAwIFIKL1NpemUgOAo+PgpzdGFydHhyZWYKMTA5MgolJUVPRgo=";

function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

async function register(page: Page, fullName = "E2E User"): Promise<string> {
  const email = uniqueEmail("e2e");
  await page.goto("/register");
  await page.getByLabel("Full name").fill(fullName);
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: /Create account/i }).click();

  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "Good to see you" })).toBeVisible();
  return email;
}

test("protects routes and supports register, logout, and login", async ({ page }) => {
  await page.goto("/documents");
  await expect(page).toHaveURL(/\/login\?next=/);

  await page.getByRole("link", { name: "Create an account" }).click();
  await expect(page).toHaveURL(/\/register$/);

  const email = await register(page, "Frontend E2E");

  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page.getByText(email)).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/, { timeout: 30_000 });

  await page.goto("/login");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: /Sign in/i }).click();
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 30_000 });
});

test("uploads, summarizes, and chats with citations", async ({ page }) => {
  await register(page, "Document E2E");

  await page.getByRole("link", { name: "Upload PDF", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Upload a document" })).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles({
    name: "e2e-contract.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from(PDF_BASE64, "base64"),
  });
  await page.getByRole("button", { name: /Upload and process/i }).click();
  await expect(page.getByRole("heading", { name: "Upload complete" })).toBeVisible({
    timeout: 30_000,
  });

  await page.getByRole("link", { name: "View document" }).click();
  await expect(page.getByText("Ready", { exact: true }).first()).toBeVisible({
    timeout: 90_000,
  });

  await page.getByRole("button", { name: "Generate short summary" }).click();
  const summary = page.locator(".prose-summary");
  await expect(summary).toBeVisible({ timeout: 30_000 });
  await expect(summary).toContainText(/\S/);

  await page.getByRole("link", { name: "Ask this document" }).click();
  await expect(page.getByRole("heading", { name: "Chat with your documents" })).toBeVisible();

  await page.getByRole("textbox", { name: "Question" }).fill("What are the payment terms?");
  await page.getByRole("button", { name: "Send question" }).click();
  const answer = page.locator("p.whitespace-pre-wrap").last();
  await expect(answer).toBeVisible({ timeout: 30_000 });
  await expect(answer).toContainText(/\S/);
  await expect(page.getByText("Sources", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /e2e-contract\.pdf/ })).toBeVisible();
});
