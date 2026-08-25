pub(crate) const HANDOFF_TARGET: &str = "com.modal3d.client.agent-handoff.v1";

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct AgentHandoff {
    pub(crate) port: u16,
    pub(crate) agent_pid: u32,
    pub(crate) desktop_pid: u32,
    pub(crate) session_token: String,
}

pub(crate) fn encode_agent_handoff(
    port: u16,
    agent_pid: u32,
    desktop_pid: u32,
    session_token: &str,
) -> Result<Vec<u8>, String> {
    let token = session_token.trim();
    if port == 0 || agent_pid == 0 || desktop_pid == 0 {
        return Err("Agent handoff 标识无效".into());
    }
    if token.len() != 64 || !token.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("Agent handoff session token 无效".into());
    }
    Ok(format!("v1\n{port}\n{agent_pid}\n{desktop_pid}\n{token}").into_bytes())
}

pub(crate) fn decode_agent_handoff(data: &[u8]) -> Result<AgentHandoff, String> {
    let text = std::str::from_utf8(data).map_err(|_| "Agent handoff 编码无效")?;
    let parts = text.split('\n').collect::<Vec<_>>();
    if parts.len() != 5 || parts[0] != "v1" {
        return Err("Agent handoff 版本无效".into());
    }
    let port = parts[1]
        .parse::<u16>()
        .map_err(|_| "Agent handoff 端口无效")?;
    let agent_pid = parts[2]
        .parse::<u32>()
        .map_err(|_| "Agent handoff agent PID 无效")?;
    let desktop_pid = parts[3]
        .parse::<u32>()
        .map_err(|_| "Agent handoff desktop PID 无效")?;
    let session_token = parts[4].trim().to_string();
    encode_agent_handoff(port, agent_pid, desktop_pid, &session_token)?;
    Ok(AgentHandoff {
        port,
        agent_pid,
        desktop_pid,
        session_token,
    })
}

#[cfg(test)]
mod tests {
    use super::{decode_agent_handoff, encode_agent_handoff, AgentHandoff, HANDOFF_TARGET};

    #[test]
    fn target_is_stable() {
        assert_eq!(HANDOFF_TARGET, "com.modal3d.client.agent-handoff.v1");
    }

    #[test]
    fn payload_roundtrip() {
        let token = "a".repeat(64);
        let data = encode_agent_handoff(48123, 1234, 5678, &token).unwrap();
        assert_eq!(
            decode_agent_handoff(&data).unwrap(),
            AgentHandoff {
                port: 48123,
                agent_pid: 1234,
                desktop_pid: 5678,
                session_token: token
            }
        );
    }

    #[test]
    fn payload_rejects_invalid_values() {
        assert!(encode_agent_handoff(0, 1, 2, &"a".repeat(64)).is_err());
        assert!(encode_agent_handoff(48123, 0, 2, &"a".repeat(64)).is_err());
        assert!(encode_agent_handoff(48123, 1, 2, "not-a-token").is_err());
        assert!(decode_agent_handoff(b"v2\n48123\n1\n2\ninvalid").is_err());
    }
}
