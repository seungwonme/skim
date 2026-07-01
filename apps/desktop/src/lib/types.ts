export type Platform = "youtube" | "threads" | "x" | "linkedin" | "reddit";

export interface AppOverview {
  workspaceRoot: string;
  dbPath: string;
  sessionDir: string;
  postsCount: number;
  trackedSourcesCount: number;
  credentialsCount: number;
}

export interface TrackedSource {
  id: number;
  platform: string;
  sourceType: string;
  displayName: string;
  canonicalId: string;
  handleOrUrl: string | null;
  isEnabled: boolean;
  focusLevel: number;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface TrackedSourceInput {
  id?: number;
  platform: string;
  sourceType: string;
  displayName: string;
  canonicalId: string;
  handleOrUrl?: string | null;
  isEnabled: boolean;
  focusLevel: number;
  notes?: string | null;
}

export interface PlatformCredential {
  id: number;
  platform: string;
  accountLabel: string;
  loginIdentifier: string;
  secretService: string;
  secretAccount: string;
  sessionPath: string | null;
  sessionStatus: string;
  lastVerifiedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface CredentialInput {
  id?: number;
  platform: string;
  accountLabel: string;
  loginIdentifier: string;
  password: string;
}

export interface SessionStatus {
  platform: string;
  path: string;
  status: string;
  cookieCount: number;
  modifiedAt: string | null;
  ageDays: number | null;
}

export interface SearchFilters {
  platform?: string;
  authorQuery?: string;
  keyword?: string;
  startDate?: string;
  endDate?: string;
  minLikes?: number;
  requireMarkdown: boolean;
  limit?: number;
}

export interface PostRecord {
  id: number;
  platform: string;
  source: string | null;
  externalId: string | null;
  author: string;
  title: string | null;
  content: string;
  url: string | null;
  timestamp: string | null;
  likes: number | null;
  comments: number | null;
  reposts: number | null;
  views: number | null;
  summary: string | null;
  contentMarkdown: string | null;
  wordCount: number | null;
  extra: string | null;
  crawledAt: string;
}

export interface SearchPostsResult {
  items: PostRecord[];
  totalCount: number;
}

export interface FeedImportItem {
  displayName: string;
  canonicalId: string;
  handleOrUrl: string;
}

export interface FeedImportResult {
  platform: string;
  total: number;
  newCount: number;
  skippedCount: number;
  insertedCount?: number;
  items: FeedImportItem[];
}
