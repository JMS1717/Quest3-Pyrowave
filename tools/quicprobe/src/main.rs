//! Can QUIC carry ALVR's video bitrate to this headset?
//!
//! ALVR's UDP backend manages 4.37 fps at 600 Mbps, because it writes one datagram per shard --
//! roughly 53,000 syscalls a second -- and never batches. QUIC runs over UDP too, so it inherits
//! that risk unless the stack uses segmentation offload (GSO) to hand the kernel many datagrams
//! per call. Whether that works on this Windows sender and this Android receiver is the single
//! fact that decides if a QUIC backend is worth building, and it is cheap to measure here rather
//! than expensive to discover after integrating.
//!
//!   headset: quicprobe sink 0.0.0.0:9950
//!   PC:      quicprobe push <headset-ip>:9950 --mbytes 512
//!
//! The headset listens and the PC dials out, which is the opposite of how ALVR's stream socket is
//! arranged. It is deliberate: the PC's Domain firewall profile is enabled and drops inbound
//! UDP on any port without an explicit rule, so a PC-side listener would need a firewall change to
//! measure. Dialling out needs none, and the data still flows PC -> headset, which is the
//! direction video takes and the only one that matters here.
//!
//! The sender pushes a fixed number of megabytes down one unidirectional stream as fast as the
//! connection allows; the receiver reports achieved throughput. Streams rather than datagrams
//! because that is what video would use: ALVR's shards are already ordered and reassembled.
use std::{net::SocketAddr, sync::Arc, time::Instant};

use anyhow::{bail, Context, Result};
use quinn::{ClientConfig, Endpoint, ServerConfig, TransportConfig, VarInt};

/// 512 MiB at 600 Mbps is about 7 s -- long enough to leave the slow-start ramp behind, short
/// enough that the headset does not heat appreciably during a probe.
const DEFAULT_MBYTES: u64 = 512;
const CHUNK: usize = 64 * 1024;

fn transport() -> TransportConfig {
    let mut cfg = TransportConfig::default();
    // Flow control, not congestion control, is what caps a single stream at high bandwidth-delay
    // products. Defaults are ~1 MB and would throttle long before 600 Mbps on any real RTT.
    cfg.stream_receive_window(VarInt::from_u32(64 * 1024 * 1024));
    cfg.receive_window(VarInt::from_u32(128 * 1024 * 1024));
    cfg.send_window(128 * 1024 * 1024);
    cfg
}

fn server_config() -> Result<ServerConfig> {
    let cert = rcgen::generate_simple_self_signed(vec!["quicprobe".into()])?;
    let key = rustls::pki_types::PrivatePkcs8KeyDer::from(cert.key_pair.serialize_der());
    let chain = vec![rustls::pki_types::CertificateDer::from(cert.cert)];
    let mut cfg = ServerConfig::with_single_cert(chain, key.into())?;
    cfg.transport_config(Arc::new(transport()));
    Ok(cfg)
}

/// Accepts the probe's own self-signed certificate. This is a throughput measurement on a LAN,
/// not an authenticated channel; a real backend would pin the certificate the way ALVR's control
/// socket already pairs devices.
#[derive(Debug)]
struct AcceptAny;

impl rustls::client::danger::ServerCertVerifier for AcceptAny {
    fn verify_server_cert(
        &self,
        _end_entity: &rustls::pki_types::CertificateDer<'_>,
        _intermediates: &[rustls::pki_types::CertificateDer<'_>],
        _server_name: &rustls::pki_types::ServerName<'_>,
        _ocsp: &[u8],
        _now: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        rustls::crypto::ring::default_provider()
            .signature_verification_algorithms
            .supported_schemes()
    }
}

fn client_config() -> Result<ClientConfig> {
    let crypto = rustls::ClientConfig::builder_with_provider(
        rustls::crypto::ring::default_provider().into(),
    )
    .with_safe_default_protocol_versions()?
    .dangerous()
    .with_custom_certificate_verifier(Arc::new(AcceptAny))
    .with_no_client_auth();

    let mut cfg = ClientConfig::new(Arc::new(quinn::crypto::rustls::QuicClientConfig::try_from(
        crypto,
    )?));
    cfg.transport_config(Arc::new(transport()));
    Ok(cfg)
}

async fn run_server(addr: SocketAddr, bytes: u64) -> Result<()> {
    let endpoint = Endpoint::server(server_config()?, addr)?;
    println!("listening on {addr}, will send {} MiB per connection", bytes / (1 << 20));

    while let Some(incoming) = endpoint.accept().await {
        let conn = incoming.await?;
        println!("connected: {}", conn.remote_address());
        let mut stream = conn.open_uni().await?;
        let payload = vec![0xA5u8; CHUNK];

        let start = Instant::now();
        let mut sent = 0u64;
        while sent < bytes {
            let n = CHUNK.min((bytes - sent) as usize);
            stream.write_all(&payload[..n]).await?;
            sent += n as u64;
        }
        stream.finish()?;
        stream.stopped().await.ok();
        let secs = start.elapsed().as_secs_f64();
        let stats = conn.stats();
        println!(
            "sent {} MiB in {secs:.2} s = {:.1} Mbps",
            sent / (1 << 20),
            (sent as f64 * 8.0) / secs / 1e6
        );
        println!("udp_tx: datagrams {} bytes {} ios {}",
            stats.udp_tx.datagrams, stats.udp_tx.bytes, stats.udp_tx.ios);
        // The number that decides whether a QUIC backend is worth building. ALVR's UDP backend is
        // unusable here at exactly 1 datagram per syscall; if the sender's GSO gets this well above
        // 1.0, QUIC avoids the wall that UDP hit rather than walking into it.
        if stats.udp_tx.ios > 0 {
            println!("datagrams per syscall: {:.2}",
                stats.udp_tx.datagrams as f64 / stats.udp_tx.ios as f64);
        }
        println!("path: rtt {:?} lost_packets {} congestion_events {}",
            stats.path.rtt, stats.path.lost_packets, stats.path.congestion_events);
        // one measurement per run: the headset side prints the number that matters
        break;
    }
    Ok(())
}

async fn run_client(addr: SocketAddr) -> Result<()> {
    let mut endpoint = Endpoint::client("0.0.0.0:0".parse()?)?;
    endpoint.set_default_client_config(client_config()?);

    let conn = endpoint
        .connect(addr, "quicprobe")?
        .await
        .context("connect failed")?;
    println!("connected to {addr}");

    let mut stream = conn.accept_uni().await?;
    let mut buffer = vec![0u8; CHUNK];
    let mut received = 0u64;
    // Timed from the first byte, not from the connect: handshake and stream setup are not part of
    // the steady-state throughput this is trying to measure.
    let mut start = None;
    while let Some(n) = stream.read(&mut buffer).await? {
        if start.is_none() {
            start = Some(Instant::now());
        }
        received += n as u64;
    }
    let secs = start.map(|s| s.elapsed().as_secs_f64()).unwrap_or(f64::NAN);
    let stats = conn.stats();
    println!(
        "received {} MiB in {secs:.2} s = {:.1} Mbps",
        received / (1 << 20),
        (received as f64 * 8.0) / secs / 1e6
    );
    println!(
        "path: rtt {:?} cwnd {} lost_packets {} congestion_events {}",
        stats.path.rtt, stats.path.cwnd, stats.path.lost_packets, stats.path.congestion_events
    );
    println!("udp_rx: datagrams {} bytes {} ios {}",
        stats.udp_rx.datagrams, stats.udp_rx.bytes, stats.udp_rx.ios);
    // datagrams-per-io is the whole question: 1.0 means every packet cost a syscall, which is what
    // makes ALVR's UDP backend unusable. Anything well above 1.0 means the stack is batching.
    if stats.udp_rx.ios > 0 {
        println!("datagrams per syscall: {:.2}",
            stats.udp_rx.datagrams as f64 / stats.udp_rx.ios as f64);
    }
    Ok(())
}

/// QUIC server that receives. The headset runs this: it listens, accepts one unidirectional
/// stream and drains it, reporting how fast the bytes arrived and how many datagrams the kernel
/// handed over per syscall -- which on the receive side means GRO.
async fn run_sink(addr: SocketAddr) -> Result<()> {
    let endpoint = Endpoint::server(server_config()?, addr)?;
    println!("listening on {addr}");

    if let Some(incoming) = endpoint.accept().await {
        let conn = incoming.await?;
        println!("connected: {}", conn.remote_address());
        let mut stream = conn.accept_uni().await?;
        let mut buffer = vec![0u8; CHUNK];
        let mut received = 0u64;
        // Timed from the first byte: handshake and stream setup are not steady-state throughput.
        let mut start = None;
        while let Some(n) = stream.read(&mut buffer).await? {
            if start.is_none() {
                start = Some(Instant::now());
            }
            received += n as u64;
        }
        let secs = start.map(|s| s.elapsed().as_secs_f64()).unwrap_or(f64::NAN);
        let stats = conn.stats();
        println!(
            "received {} MiB in {secs:.2} s = {:.1} Mbps",
            received / (1 << 20),
            (received as f64 * 8.0) / secs / 1e6
        );
        println!("path: rtt {:?} cwnd {} lost_packets {} congestion_events {}",
            stats.path.rtt, stats.path.cwnd, stats.path.lost_packets,
            stats.path.congestion_events);
        println!("udp_rx: datagrams {} bytes {} ios {}",
            stats.udp_rx.datagrams, stats.udp_rx.bytes, stats.udp_rx.ios);
        if stats.udp_rx.ios > 0 {
            println!("datagrams per syscall (rx/GRO): {:.2}",
                stats.udp_rx.datagrams as f64 / stats.udp_rx.ios as f64);
        }
    }
    Ok(())
}

/// QUIC client that sends. The PC runs this.
async fn run_push(addr: SocketAddr, bytes: u64) -> Result<()> {
    let mut endpoint = Endpoint::client("0.0.0.0:0".parse()?)?;
    endpoint.set_default_client_config(client_config()?);

    let conn = endpoint.connect(addr, "quicprobe")?.await.context("connect failed")?;
    println!("connected to {addr}, sending {} MiB", bytes / (1 << 20));

    let mut stream = conn.open_uni().await?;
    let payload = vec![0xA5u8; CHUNK];
    let start = Instant::now();
    let mut sent = 0u64;
    while sent < bytes {
        let n = CHUNK.min((bytes - sent) as usize);
        stream.write_all(&payload[..n]).await?;
        sent += n as u64;
    }
    stream.finish()?;
    stream.stopped().await.ok();

    let secs = start.elapsed().as_secs_f64();
    let stats = conn.stats();
    println!("sent {} MiB in {secs:.2} s = {:.1} Mbps",
        sent / (1 << 20), (sent as f64 * 8.0) / secs / 1e6);
    println!("udp_tx: datagrams {} bytes {} ios {}",
        stats.udp_tx.datagrams, stats.udp_tx.bytes, stats.udp_tx.ios);
    // The number that decides whether a QUIC backend is worth building. ALVR's UDP backend is
    // unusable here at exactly 1 datagram per syscall; well above 1.0 means the stack is batching
    // via segmentation offload and QUIC avoids that wall rather than walking into it.
    if stats.udp_tx.ios > 0 {
        println!("datagrams per syscall (tx/GSO): {:.2}",
            stats.udp_tx.datagrams as f64 / stats.udp_tx.ios as f64);
    }
    println!("path: rtt {:?} lost_packets {} congestion_events {}",
        stats.path.rtt, stats.path.lost_packets, stats.path.congestion_events);
    Ok(())
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        bail!("usage: quicprobe <sink|push|server|client> <addr> [--mbytes N]");
    }
    let addr: SocketAddr = args[2].parse().context("bad address")?;
    let mbytes = args
        .iter()
        .position(|a| a == "--mbytes")
        .and_then(|i| args.get(i + 1))
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(DEFAULT_MBYTES);

    rustls::crypto::ring::default_provider().install_default().ok();

    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build()?;
    match args[1].as_str() {
        // listener receives, dialler sends: no inbound firewall rule needed on the PC
        "sink" => runtime.block_on(run_sink(addr)),
        "push" => runtime.block_on(run_push(addr, mbytes * (1 << 20))),
        // listener sends, dialler receives: kept for a loopback sanity check
        "server" => runtime.block_on(run_server(addr, mbytes * (1 << 20))),
        "client" => runtime.block_on(run_client(addr)),
        other => bail!("unknown mode {other}"),
    }
}
