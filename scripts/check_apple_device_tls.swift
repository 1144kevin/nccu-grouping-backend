// macOS only. Offline Apple trust evaluation using PUBLIC certificates.
// No network requests, OS trust changes, keychain installation or private keys.
import Foundation
import Security
import CryptoKit

struct Facts: Decodable {
  let host: String
  let ca_sha256: String
  let leaf_sha256: String
}

func sha256(_ data: Data) -> String {
  SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

func evaluate(_ certificates: [SecCertificate], anchor: SecCertificate, host: String) throws -> [String: Any] {
  var trust: SecTrust?
  let policy = SecPolicyCreateSSL(true, host as CFString)
  let created = SecTrustCreateWithCertificates(certificates as CFArray, policy, &trust)
  guard created == errSecSuccess, let trust else {
    throw NSError(domain: "AppleTLSProbe", code: Int(created))
  }
  guard SecTrustSetAnchorCertificates(trust, [anchor] as CFArray) == errSecSuccess,
        SecTrustSetAnchorCertificatesOnly(trust, true) == errSecSuccess,
        SecTrustSetNetworkFetchAllowed(trust, false) == errSecSuccess else {
    throw NSError(domain: "AppleTLSProbe", code: 1)
  }
  var error: CFError?
  let valid = SecTrustEvaluateWithError(trust, &error)
  var result: [String: Any] = ["host": host, "valid": valid]
  if let error {
    result["error_domain"] = CFErrorGetDomain(error) as String
    result["error_code"] = CFErrorGetCode(error)
    result["error_description"] = CFErrorCopyDescription(error) as String
  }
  return result
}

do {
  guard CommandLine.arguments.count == 2 else {
    throw NSError(domain: "Usage: swift check_apple_device_tls.swift PUBLIC_FOLDER", code: 2)
  }
  let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
  let facts = try JSONDecoder().decode(Facts.self, from: Data(contentsOf: directory.appendingPathComponent("tls-facts.json")))
  let caData = try Data(contentsOf: directory.appendingPathComponent("ca.cer"))
  let leafData = try Data(contentsOf: directory.appendingPathComponent("server.cer"))
  guard sha256(caData) == facts.ca_sha256, sha256(leafData) == facts.leaf_sha256,
        let ca = SecCertificateCreateWithData(nil, caData as CFData),
        let leaf = SecCertificateCreateWithData(nil, leafData as CFData) else {
    throw NSError(domain: "Public certificate fingerprint/format mismatch", code: 3)
  }
  let valid = try evaluate([leaf, ca], anchor: ca, host: facts.host)
  let negative = try evaluate([leaf, ca], anchor: ca, host: "203.0.113.254")
  let result: [String: Any] = [
    "scope": "offline explicit known-CA evaluation only; not iPhone system trust or Expo Go verification",
    "correct_host": valid,
    "wrong_host": negative,
    "system_trust_modified": false,
    "network_requests": false
  ]
  let output = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
  print(String(decoding: output, as: UTF8.self))
  if valid["valid"] as? Bool != true || negative["valid"] as? Bool != false { exit(1) }
} catch {
  FileHandle.standardError.write(Data("Offline Apple TLS probe failed: \(error)\n".utf8))
  exit(2)
}
