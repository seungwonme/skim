import Foundation
import Security

public enum KeychainStore {
    public static func secretService(platform: String) -> String {
        "skim.desktop.\(platform)"
    }

    public static func save(password: String, service: String, account: String) throws {
        let data = Data(password.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: data
        ]

        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess {
            return
        }
        if updateStatus != errSecItemNotFound {
            throw SkimDatabaseError.keychainFailed(status: updateStatus)
        }

        var addQuery = query
        addQuery[kSecValueData as String] = data
        let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw SkimDatabaseError.keychainFailed(status: addStatus)
        }
    }

    public static func delete(service: String, account: String) throws {
        let status = SecItemDelete([
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ] as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw SkimDatabaseError.keychainFailed(status: status)
        }
    }
}
