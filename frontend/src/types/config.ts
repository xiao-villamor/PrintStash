export interface SetupStatus {
  configured: boolean;
  user_count: number;
  default_data_dir: string;
  default_thumb_dir: string;
  current_data_dir: string;
  current_thumb_dir: string;
  current_storage_backend: string;
  current_s3_bucket: string;
  current_s3_endpoint_url: string;
  current_s3_region: string;
  current_backup_retention_days: number;
  current_backup_s3_bucket: string;
  current_backup_s3_endpoint_url: string;
  current_backup_s3_region: string;
  configured_at: string | null;
}

export interface SetupRequest {
  username: string;
  password: string;
  email?: string;
  storage_backend?: string;
  data_dir?: string;
  thumb_dir?: string;
  s3_bucket?: string;
  s3_endpoint_url?: string;
  s3_region?: string;
  s3_access_key?: string;
  s3_secret_key?: string;
  backup_retention_days?: number;
  backup_s3_bucket?: string;
  backup_s3_endpoint_url?: string;
  backup_s3_region?: string;
  backup_s3_access_key?: string;
  backup_s3_secret_key?: string;
}

export interface SetupResponse {
  configured: boolean;
  user_id: number;
  username: string;
  storage_backend: string;
  data_dir: string;
  thumb_dir: string;
  access_token: string;
  token_type: string;
}

export interface VaultConfigRead {
  storage_backend: string;
  data_dir: string;
  thumb_dir: string;
  s3_bucket: string;
  s3_endpoint_url: string;
  s3_region: string;
  s3_access_key: string;
  s3_secret_key: string;
  has_s3_access_key: boolean;
  has_s3_secret_key: boolean;
  backup_retention_days: number;
  trash_retention_days: number;
  backup_s3_bucket: string;
  backup_s3_endpoint_url: string;
  backup_s3_region: string;
  backup_s3_access_key: string;
  backup_s3_secret_key: string;
  has_backup_s3_access_key: boolean;
  has_backup_s3_secret_key: boolean;
  has_backup_s3: boolean;
}

export interface VaultConfigUpdate {
  storage_backend?: string;
  data_dir?: string;
  thumb_dir?: string;
  s3_bucket?: string;
  s3_endpoint_url?: string;
  s3_region?: string;
  s3_access_key?: string;
  s3_secret_key?: string;
  backup_retention_days?: number;
  trash_retention_days?: number;
  backup_s3_bucket?: string;
  backup_s3_endpoint_url?: string;
  backup_s3_region?: string;
  backup_s3_access_key?: string;
  backup_s3_secret_key?: string;
}
