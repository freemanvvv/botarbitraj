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

// Load wallet from private key (bs58 or byte array)
export function loadWallet(privateKey) {
  try {
    let decoded
    if (privateKey.startsWith('[')) {
      // JSON array format: [1,2,3,...]
      decoded = Uint8Array.from(JSON.parse(privateKey))
    } else {
      // Base58 format
      decoded = bs58.decode(privateKey)
    }
    wallet = Keypair.fromSecretKey(decoded)
    return wallet
  } catch (err) {
    console.error('Failed to load wallet:', err.message)
    return null
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

  // Wait for confirmation
  await conn.confirmTransaction(txId, 'confirmed')
  return txId
}

// Get wallet balance (in SOL)
export async function getWalletBalance() {
  if (!wallet) return 0
  const conn = getConnection()
  const balance = await conn.getBalance(wallet.publicKey)
  return balance / 1e9 // Convert lamports to SOL
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
    // Parse token account data
    const accountInfo = await conn.getTokenAccountBalance(tokenAccounts.value[0].pubkey)
    return accountInfo.value.uiAmount || 0
  } catch {
    return 0
  }
}
