// Config
export const CONFIG = {
  // Solana RPC (use Helius/QuickNode for production)
  SOLANA_RPC: process.env.SOLANA_RPC || 'https://api.mainnet-beta.solana.com',
  SOLANA_WSS: process.env.SOLANA_WSS || 'wss://api.mainnet-beta.solana.com',

  // Jupiter API
  JUPITER_API: 'https://quote-api.jup.ag/v6',
  JUPITER_PRICE_API: 'https://api.jup.ag/price/v2',

  // Wallet
  PRIVATE_KEY: process.env.PRIVATE_KEY || null,

  // Trading
  MIN_PROFIT_BPS: process.env.MIN_PROFIT_BPS || 30, // 0.3%
  TRADE_AMOUNT_USDC: process.env.TRADE_AMOUNT_USDC || 10, // USDC per leg
  SLIPPAGE_BPS: process.env.SLIPPAGE_BPS || 50, // 0.5%
  SCAN_INTERVAL_MS: process.env.SCAN_INTERVAL_MS || 3000, // 3s

  // Server
  PORT: process.env.PORT || 3001,

  // Token addresses (Solana mainnet)
  TOKENS: {
    USDC: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
    SOL: 'So11111111111111111111111111111111111111112',
    RAY: '4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R',
    JUP: 'JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN',
    BONK: 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263',
    PYTH: 'HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3',
    JTO: 'jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL',
    ORCA: 'orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE',
    WIF: 'EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm',
  },

  // Triangle routes (token mint addresses)
  TRIANGLES: [
    ['EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'So11111111111111111111111111111111111111112', '4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R'], // USDC → SOL → RAY → USDC
    ['EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'So11111111111111111111111111111111111111112', 'JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN'], // USDC → SOL → JUP → USDC
    ['EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'So11111111111111111111111111111111111111112', 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263'], // USDC → SOL → BONK → USDC
    ['EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', '4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R', 'JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN'], // USDC → RAY → JUP → USDC
  ],
}
