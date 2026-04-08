import { invoke } from "@tauri-apps/api/core";

import type {
  AppOverview,
  CredentialInput,
  ExportResult,
  FeedImportResult,
  PlatformCredential,
  SearchFilters,
  SearchPostsResult,
  SessionStatus,
  TrackedSource,
  TrackedSourceInput,
} from "./types";

export function getAppOverview() {
  return invoke<AppOverview>("get_app_overview");
}

export function listTrackedSources() {
  return invoke<TrackedSource[]>("list_tracked_sources");
}

export function upsertTrackedSource(input: TrackedSourceInput) {
  return invoke<TrackedSource>("upsert_tracked_source", { input });
}

export function deleteTrackedSource(id: number) {
  return invoke<void>("delete_tracked_source", { id });
}

export function listCredentials() {
  return invoke<PlatformCredential[]>("list_credentials");
}

export function saveCredential(input: CredentialInput) {
  return invoke<PlatformCredential>("save_credential", { input });
}

export function deleteCredential(id: number) {
  return invoke<void>("delete_credential", { id });
}

export function verifySession(platform: string, sessionPath?: string | null) {
  return invoke<SessionStatus>("verify_session", {
    platform,
    sessionPath: sessionPath ?? null,
  });
}

export function startLogin(credentialId: number) {
  return invoke<string>("start_login", { credentialId });
}

export function previewFeedImport() {
  return invoke<FeedImportResult>("preview_feed_import");
}

export function importFeedSources() {
  return invoke<FeedImportResult>("import_feed_sources");
}

export function searchPosts(filters: SearchFilters) {
  return invoke<SearchPostsResult>("search_posts", { filters });
}

export function exportPosts(ids: number[], format: string, destination: string) {
  return invoke<ExportResult>("export_posts", {
    request: { ids, format, destination },
  });
}
