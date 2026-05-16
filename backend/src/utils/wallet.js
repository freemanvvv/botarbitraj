import { Connection, Keypair, PublicKey, VersionedTransaction } from '@solana/web3.js'
import bs58 from 'bs58'
import { CONFIG } from '../config/index.js'

let connection = null
let wallet = null

export function getConnection() {
  if (!connection) {
    connection = new Connection(CONFIG.SOLANA_RPC, 'confirmed')
  }
  return connection
}

// Загружает кошелёк из private key в разных форматах
export function loadWallet(privateKey) {
  try {
    let decoded

    // 1. JSON array: [1,2,3,...] (64 числа — полный secret key)
    if (privateKey.startsWith('[')) {
      decoded = Uint8Array.from(JSON.parse(privateKey))
    }
    // 2. Hex строка (128 hex символов = 64 байта полный secret key)
    else if (/^[0-9a-fA-F]{128}$/.test(privateKey.trim())) {
      decoded = Buffer.from(privateKey.trim(), 'hex')
    }
    else {
      // Всё остальное — base58 (32 байта raw key или 64 байта полный secret key)
      decoded = bs58.decode(privateKey.trim())
    }

    // 32 байта — это raw private key, не полный secret key
    if (decoded.length === 32) {
      wallet = Keypair.fromSeed(decoded)
    } else if (decoded.length === 64) {
      wallet = Keypair.fromSecretKey(decoded)
    } else {
      throw new Error(`Неверная длина ключа: ${decoded.length} байт (нужно 32 или 64)`)
    }

    console.log(`✅ Wallet loaded: ${wallet.publicKey.toBase58()}`)
    return wallet
  } catch (err) {
    console.error('Failed to load wallet:', err.message)

    // Даём пользователю понятную подсказку
    const trimmed = privateKey.trim()
    const hints = []
    if (trimmed.includes(' ')) hints.push('содержит лишние пробелы')
    if (trimmed.includes("'") || trimmed.includes('"')) hints.push('содержит лишние кавычки')
    if (trimmed.length < 10) hints.push('слишком короткий')
    if (trimmed.includes('_') || trimmed.includes('-')) hints.push('возможно это seed-фраза, а не private key')

    const hint = hints.length > 0 ? ` (${hints.join(', ')})` : ''
    throw new Error(`Неверный формат ключа${hint}. Скопируй ключ заново из Phantom: Настройки → Export Private Key`)
  }
}

export function getWallet() {
  return wallet
}

// Sign and send a transaction from Jupiter swap response
export async function signAndSendTransaction(swapTxResponse) {
  if (!wallet) throw new Error('Wallet not loaded')

  const conn = getConnection()
  const tx = VersionedTransaction.deserialize(Buffer.from(swapTxResponse.swapTransaction, 'base64'))
  tx.sign([wallet])

  const txId = await conn.sendTransaction(tx, {
    skipPreflight: true,
    maxRetries: 3,
  })

  await conn.confirmTransaction(txId, 'confirmed')
  return txId
}

// Get wallet balance (in SOL)
export async function getWalletBalance() {
  if (!wallet) return 0
  const conn = getConnection()
  const balance = await conn.getBalance(wallet.publicKey)
  return balance / 1e9
}

// Get token account balance
export async function getTokenBalance(mint) {
  if (!wallet) return 0
  const conn = getConnection()
  try {
    const tokenAccounts = await conn.getTokenAccountsByOwner(wallet.publicKey, {
      mint: new PublicKey(mint),
    })
    if (tokenAccounts.value.length === 0) return 0
    const accountInfo = await conn.getTokenAccountBalance(tokenAccounts.value[0].pubkey)
    return accountInfo.value.uiAmount || 0
  } catch {
    return 0
  }
}
