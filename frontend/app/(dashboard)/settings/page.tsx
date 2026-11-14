"use client";

import { FormEvent, useEffect, useState } from "react";
import { Loader2, LogOut, ShieldCheck, UserRound } from "lucide-react";
import { api, getErrorMessage } from "@/lib/api";
import type { User } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { ErrorState, InlineAlert } from "@/components/states";

export default function SettingsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [fullName, setFullName] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileMessage, setProfileMessage] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [savingPassword, setSavingPassword] = useState(false);
  const [passwordMessage, setPasswordMessage] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const response = await api.auth.me();
        if (cancelled) return;
        setUser(response);
        setFullName(response.full_name);
      } catch (err) {
        if (!cancelled) setLoadError(getErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSavingProfile(true);
    setProfileError(null);
    setProfileMessage(null);
    try {
      const updated = await api.auth.updateProfile(fullName);
      setUser(updated);
      setFullName(updated.full_name);
      setProfileMessage("Profile updated.");
    } catch (err) {
      setProfileError(getErrorMessage(err));
    } finally {
      setSavingProfile(false);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPasswordError(null);
    setPasswordMessage(null);
    if (newPassword.length < 8 || !/[A-Za-z]/.test(newPassword) || !/\d/.test(newPassword)) {
      setPasswordError("Use at least 8 characters, including one letter and one number.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("New passwords do not match.");
      return;
    }
    setSavingPassword(true);
    try {
      await api.auth.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordMessage("Password changed. Other refresh sessions have been revoked.");
    } catch (err) {
      setPasswordError(getErrorMessage(err));
    } finally {
      setSavingPassword(false);
    }
  }

  async function signOut() {
    setLoggingOut(true);
    await api.auth.logout();
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="skeleton h-9 w-48" />
        <div className="skeleton h-52 w-full" />
        <div className="skeleton h-64 w-full" />
      </div>
    );
  }

  if (loadError || !user) {
    return <ErrorState message={loadError ?? "Your profile could not be loaded."} />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Account"
        title="Settings"
        description="Manage your profile, security, and current session."
      />

      <div className="grid gap-6 xl:grid-cols-[1fr_1.3fr]">
        <section className="card h-fit p-5 sm:p-6">
          <div className="flex items-center gap-4">
            <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-50 text-lg font-bold text-brand-700">
              {user.full_name
                .split(/\s+/)
                .slice(0, 2)
                .map((part) => part[0]?.toUpperCase())
                .join("")}
            </span>
            <div className="min-w-0">
              <p className="truncate text-base font-semibold text-slate-900">{user.full_name}</p>
              <p className="truncate text-sm text-slate-500">{user.email}</p>
            </div>
          </div>
          <dl className="mt-6 space-y-3 border-t border-slate-100 pt-5 text-sm">
            <div className="flex items-center justify-between gap-4">
              <dt className="text-slate-500">Member since</dt>
              <dd className="font-medium text-slate-700">{formatDate(user.created_at)}</dd>
            </div>
            <div className="flex items-center justify-between gap-4">
              <dt className="text-slate-500">Account status</dt>
              <dd className="inline-flex items-center gap-1.5 font-medium text-emerald-700">
                <ShieldCheck className="h-4 w-4" /> Active
              </dd>
            </div>
          </dl>
          <button
            type="button"
            className="btn-secondary mt-6 w-full !justify-start !text-rose-600 hover:!bg-rose-50"
            onClick={() => void signOut()}
            disabled={loggingOut}
          >
            {loggingOut ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <LogOut className="h-4 w-4" />
            )}
            Sign out
          </button>
        </section>

        <div className="space-y-6">
          <section className="card p-5 sm:p-6">
            <div className="flex items-center gap-2">
              <UserRound className="h-4 w-4 text-brand-600" />
              <h2 className="text-base font-semibold text-slate-900">Profile</h2>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              Your display name is only visible inside your workspace.
            </p>
            <form onSubmit={(event) => void saveProfile(event)} className="mt-5 space-y-4">
              {profileError && <InlineAlert>{profileError}</InlineAlert>}
              {profileMessage && <InlineAlert variant="success">{profileMessage}</InlineAlert>}
              <div>
                <label htmlFor="profile_name" className="label">
                  Full name
                </label>
                <input
                  id="profile_name"
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                  className="input"
                  minLength={2}
                  maxLength={120}
                  required
                />
              </div>
              <div>
                <label htmlFor="profile_email" className="label">
                  Email
                </label>
                <input id="profile_email" value={user.email} className="input" disabled />
                <p className="mt-1.5 text-xs text-slate-400">
                  Email changes require a verification flow and are not enabled in this MVP.
                </p>
              </div>
              <div className="flex justify-end">
                <button type="submit" className="btn-primary" disabled={savingProfile}>
                  {savingProfile && <Loader2 className="h-4 w-4 animate-spin" />}
                  Save changes
                </button>
              </div>
            </form>
          </section>

          <section className="card p-5 sm:p-6">
            <h2 className="text-base font-semibold text-slate-900">Password & security</h2>
            <p className="mt-1 text-xs text-slate-500">
              Changing your password revokes all existing refresh tokens and starts a new session.
            </p>
            <form onSubmit={(event) => void changePassword(event)} className="mt-5 space-y-4">
              {passwordError && <InlineAlert>{passwordError}</InlineAlert>}
              {passwordMessage && <InlineAlert variant="success">{passwordMessage}</InlineAlert>}
              <div>
                <label htmlFor="current_password" className="label">
                  Current password
                </label>
                <input
                  id="current_password"
                  type="password"
                  autoComplete="current-password"
                  value={currentPassword}
                  onChange={(event) => setCurrentPassword(event.target.value)}
                  className="input"
                  required
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label htmlFor="new_password" className="label">
                    New password
                  </label>
                  <input
                    id="new_password"
                    type="password"
                    autoComplete="new-password"
                    value={newPassword}
                    onChange={(event) => setNewPassword(event.target.value)}
                    className="input"
                    minLength={8}
                    required
                  />
                </div>
                <div>
                  <label htmlFor="confirm_new_password" className="label">
                    Confirm new password
                  </label>
                  <input
                    id="confirm_new_password"
                    type="password"
                    autoComplete="new-password"
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    className="input"
                    minLength={8}
                    required
                  />
                </div>
              </div>
              <div className="flex justify-end">
                <button type="submit" className="btn-primary" disabled={savingPassword}>
                  {savingPassword && <Loader2 className="h-4 w-4 animate-spin" />}
                  Update password
                </button>
              </div>
            </form>
          </section>
        </div>
      </div>
    </div>
  );
}
