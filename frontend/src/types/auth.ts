export interface LoginRequest {
  username: string;
  password?: string;
  api_key?: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token?: string | null;
  scope?: string;
  token_type: string;
}

export interface UserRead {
  id: number;
  username: string;
  email: string | null;
  is_superuser: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ApiKeyRead {
  id: number;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
}

export interface ApiKeyCreateResponse extends ApiKeyRead {
  api_key: string;
}
