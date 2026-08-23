use serde::{Deserialize, Serialize};

#[cfg(target_os = "windows")]
use std::sync::OnceLock;

#[cfg(target_os = "windows")]
const SERVICE: &str = "com.modal3d.client.modal";
#[cfg(target_os = "windows")]
const USER: &str = "modal-token";

#[derive(Deserialize)]
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub struct CredentialInput {
    token_id: String,
    token_secret: String,
}

#[derive(Serialize)]
pub struct CredentialStatus {
    supported: bool,
    stored: bool,
}

fn encode(token_id: &str, token_secret: &str) -> Result<Vec<u8>, String> {
    let token_id = token_id.trim();
    let token_secret = token_secret.trim();
    if token_id.is_empty() || token_secret.is_empty() {
        return Err("Token ID and Token Secret are required".into());
    }
    let id_len = u32::try_from(token_id.len()).map_err(|_| "Token ID is too long")?;
    let mut data = Vec::with_capacity(4 + token_id.len() + token_secret.len());
    data.extend_from_slice(&id_len.to_le_bytes());
    data.extend_from_slice(token_id.as_bytes());
    data.extend_from_slice(token_secret.as_bytes());
    Ok(data)
}

fn decode(data: &[u8]) -> Result<(String, String), String> {
    if data.len() < 4 {
        return Err("Stored Modal credential is invalid".into());
    }
    let id_len = u32::from_le_bytes(data[..4].try_into().unwrap()) as usize;
    if id_len == 0 || 4 + id_len >= data.len() {
        return Err("Stored Modal credential is invalid".into());
    }
    let token_id = String::from_utf8(data[4..4 + id_len].to_vec())
        .map_err(|_| "Stored Modal credential is invalid")?;
    let token_secret = String::from_utf8(data[4 + id_len..].to_vec())
        .map_err(|_| "Stored Modal credential is invalid")?;
    Ok((token_id, token_secret))
}

#[cfg(target_os = "windows")]
fn entry() -> Result<keyring_core::Entry, String> {
    static INIT: OnceLock<Result<(), String>> = OnceLock::new();
    INIT.get_or_init(|| {
        let store =
            windows_native_keyring_store::Store::new().map_err(|error| error.to_string())?;
        keyring_core::set_default_store(store);
        Ok(())
    })
    .clone()?;
    keyring_core::Entry::new(SERVICE, USER).map_err(|error| error.to_string())
}

#[cfg(target_os = "windows")]
pub(crate) fn load() -> Result<Option<(String, String)>, String> {
    let data = match entry()?.get_secret() {
        Ok(data) => data,
        Err(keyring_core::Error::NoEntry) => return Ok(None),
        Err(error) => return Err(error.to_string()),
    };
    decode(&data).map(Some)
}

#[cfg(not(target_os = "windows"))]
pub(crate) fn load() -> Result<Option<(String, String)>, String> {
    Ok(None)
}

#[cfg(target_os = "windows")]
fn has_stored() -> Result<bool, String> {
    match entry()?.get_secret() {
        Ok(_) => Ok(true),
        Err(keyring_core::Error::NoEntry) => Ok(false),
        Err(error) => Err(error.to_string()),
    }
}

#[cfg(not(target_os = "windows"))]
fn has_stored() -> Result<bool, String> {
    Ok(false)
}

#[tauri::command]
pub fn credentials_status() -> Result<CredentialStatus, String> {
    Ok(CredentialStatus {
        supported: cfg!(target_os = "windows"),
        stored: has_stored()?,
    })
}

#[tauri::command]
pub fn credentials_save(credentials: CredentialInput) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        let data = encode(&credentials.token_id, &credentials.token_secret)?;
        entry()?
            .set_secret(&data)
            .map_err(|error| error.to_string())
    }

    #[cfg(not(target_os = "windows"))]
    {
        let _ = credentials;
        Err("Credential persistence is only available on Windows".into())
    }
}

#[tauri::command]
pub fn credentials_clear() -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        match entry()?.delete_credential() {
            Ok(()) | Err(keyring_core::Error::NoEntry) => Ok(()),
            Err(error) => Err(error.to_string()),
        }
    }

    #[cfg(not(target_os = "windows"))]
    Err("Credential persistence is only available on Windows".into())
}

#[cfg(test)]
mod tests {
    use super::{decode, encode};

    #[test]
    fn credential_payload_roundtrip() {
        let data = encode("ak-test", "as-test").unwrap();
        assert_eq!(decode(&data).unwrap(), ("ak-test".into(), "as-test".into()));
    }

    #[test]
    fn credential_payload_rejects_invalid_data() {
        assert!(decode(&[0, 0, 0]).is_err());
        assert!(encode("", "as-test").is_err());
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn windows_credential_manager_roundtrip() {
        use std::time::{SystemTime, UNIX_EPOCH};

        let store = windows_native_keyring_store::Store::new().unwrap();
        keyring_core::set_default_store(store);
        let user = format!(
            "test-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let entry = keyring_core::Entry::new("com.modal3d.client.test", &user).unwrap();
        entry.set_secret(b"credential-test").unwrap();
        assert_eq!(entry.get_secret().unwrap(), b"credential-test");
        entry.delete_credential().unwrap();
    }
}
