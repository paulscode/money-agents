/**
 * Boltz Cooperative Taproot Claim Script
 *
 * Constructs and broadcasts a cooperative claim transaction for a Boltz
 * reverse submarine swap using Musig2 (BIP-327) key-path spending.
 *
 * Input: JSON on stdin with swap details
 * Output: JSON on stdout with { txid, txHex }
 *
 * Uses boltz-core@3.1.x (reference implementation) for:
 * - Taproot tree construction & swap output detection
 * - Musig2 nonce exchange + partial signatures via @vulpemventures/secp256k1-zkp
 * - Transaction construction + witness building via bitcoinjs-lib
 *
 * @see https://docs.boltz.exchange/v/api/lifecycle#reverse-submarine-swaps
 */
'use strict';

const crypto = require('crypto');
const { ECPairFactory } = require('ecpair');
const ecc = require('tiny-secp256k1');
const {
  constructClaimTransaction,
  detectSwap,
  targetFee,
  Networks,
  Musig,
  TaprootUtils,
  OutputType,
} = require('boltz-core');
const { Transaction, address, initEccLib } = require('bitcoinjs-lib');
const http = require('http');
const https = require('https');

// Optional SOCKS proxy support for Tor routing
let SocksProxyAgent;
try {
  SocksProxyAgent = require('socks-proxy-agent').SocksProxyAgent;
} catch {
  // socks-proxy-agent not installed — proxy will not be available
}

// Initialize ECC library for bitcoinjs-lib
initEccLib(ecc);
const ECPair = ECPairFactory(ecc);

// Shared proxy agent instance (initialized in main() if socksProxy is provided)
let proxyAgent = null;

/**
 * Make an HTTP(S) request, optionally routed through a SOCKS5 proxy (Tor).
 * When a proxy agent is configured, all requests go through the Tor network
 * so the user's IP is never revealed to Boltz Exchange.
 */
async function httpRequest(url, method, body = null) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const isHttps = urlObj.protocol === 'https:';
    const lib = isHttps ? https : http;

    const options = {
      hostname: urlObj.hostname,
      port: urlObj.port || (isHttps ? 443 : 80),
      path: urlObj.pathname + urlObj.search,
      method,
      headers: { 'Content-Type': 'application/json' },
      timeout: 60000,
    };

    // Route through SOCKS5 proxy (Tor) if configured
    if (proxyAgent) {
      options.agent = proxyAgent;
    }

    const req = lib.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, data: JSON.parse(data) });
        } catch {
          resolve({ status: res.statusCode, data });
        }
      });
    });

    req.on('timeout', () => {
      req.destroy();
      reject(new Error(`HTTP request timed out: ${method} ${url}`));
    });
    req.on('error', reject);
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

/**
 * Parse swap tree from Boltz API response into boltz-core format.
 * Boltz returns hex-encoded leaf outputs; boltz-core expects Buffers.
 */
function parseSwapTree(swapTreeJson) {
  const tree = {
    claimLeaf: {
      version: swapTreeJson.claimLeaf.version,
      output: Buffer.from(swapTreeJson.claimLeaf.output, 'hex'),
    },
    refundLeaf: {
      version: swapTreeJson.refundLeaf.version,
      output: Buffer.from(swapTreeJson.refundLeaf.output, 'hex'),
    },
  };
  // boltz-core expects a `tree` property (array of [claimLeaf, refundLeaf])
  tree.tree = [tree.claimLeaf, tree.refundLeaf];
  return tree;
}

/**
 * Main cooperative claim flow:
 * 1. Initialize secp256k1-zkp for Musig2 operations
 * 2. Parse lockup transaction and find swap output via tweaked Musig key
 * 3. Build cooperative claim transaction (dummy signature for fee calc)
 * 4. Create Musig2 session, exchange nonces with Boltz
 * 5. Aggregate partial signatures into final Schnorr signature
 * 6. Set witness and broadcast via Boltz API
 */
async function main() {
  // Read input from stdin
  const inputChunks = [];
  for await (const chunk of process.stdin) {
    inputChunks.push(chunk);
  }
  const input = JSON.parse(Buffer.concat(inputChunks).toString());

  const {
    boltzUrl,
    swapId,
    preimage,
    claimPrivateKey,
    refundPublicKey,
    swapTree: swapTreeJson,
    lockupTxHex,
    destinationAddress,
    socksProxy,
  } = input;

  // ── Step 0: Initialize SOCKS proxy for Tor routing (if configured) ──
  if (socksProxy) {
    if (!SocksProxyAgent) {
      throw new Error(
        'SOCKS proxy requested but socks-proxy-agent is not installed. ' +
        'Run: npm install socks-proxy-agent'
      );
    }
    proxyAgent = new SocksProxyAgent(socksProxy);
    process.stderr.write(`[claim] Routing through Tor proxy: ${socksProxy}\n`);
  }

  // ── Step 1: Initialize secp256k1-zkp (async WASM init) ──
  const zkpInit = require('@vulpemventures/secp256k1-zkp');
  const secp = await (zkpInit.default || zkpInit)();

  // ── Step 2: Derive keys and parse inputs ──
  const keys = ECPair.fromPrivateKey(Buffer.from(claimPrivateKey, 'hex'));
  const preimageBuffer = Buffer.from(preimage, 'hex');
  const refundPubKey = Buffer.from(refundPublicKey, 'hex');
  const tree = parseSwapTree(swapTreeJson);
  const lockupTx = Transaction.fromHex(lockupTxHex);

  // ── Step 2: Create Musig2 session and tweak for Taproot ──
  // The Musig class from boltz-core@3.1.x takes (secp, key, sessionId, publicKeys)
  // Key ordering: Boltz uses [refundPubKey, claimPubKey] for reverse swaps
  // (Boltz's refund key first, our claim key second)
  const musig = new Musig(secp, keys, crypto.randomBytes(32), [
    refundPubKey,
    keys.publicKey,
  ]);

  // tweakMusig mutates the musig's internal keyaggCache for Taproot spending
  // and returns the x-only tweaked aggregate public key
  const tweakedKey = TaprootUtils.tweakMusig(musig, tree.tree);

  // ── Step 3: Find the swap output using the tweaked key ──
  const swapOutput = detectSwap(tweakedKey, lockupTx);
  if (!swapOutput) {
    throw new Error(
      `Could not find swap output in lockup transaction ${lockupTx.getId()}`
    );
  }

  // ── Step 4: Build cooperative claim transaction ──
  // With cooperative: true, boltz-core sets a dummy 64-byte signature for fee estimation.
  // We'll replace it with the real Musig2 aggregated signature later.
  const destinationScript = address.toOutputScript(
    destinationAddress,
    Networks.bitcoinMainnet
  );
  const claimTx = targetFee(2, (fee) =>
    constructClaimTransaction(
      [
        {
          ...swapOutput,
          txHash: lockupTx.getHash(),
          keys,
          preimage: preimageBuffer,
          cooperative: true,
          type: OutputType.Taproot,
          swapTree: tree,
        },
      ],
      destinationScript,
      fee,
      true // isRbf
    )
  );

  // ── Step 5: Compute sighash for Taproot key-path spend ──
  const sigHash = claimTx.hashForWitnessV1(
    0,
    [swapOutput.script],
    [swapOutput.value],
    Transaction.SIGHASH_DEFAULT
    // No leafHash → key-path spend
  );

  // ── Step 6: Get our public nonce and request Boltz's ──
  const ourPubNonce = Buffer.from(musig.getPublicNonce()).toString('hex');

  const claimResponse = await httpRequest(
    `${boltzUrl}/swap/reverse/${swapId}/claim`,
    'POST',
    {
      preimage,
      pubNonce: ourPubNonce,
      transaction: claimTx.toHex(),
      index: 0,
    }
  );

  if (claimResponse.status !== 200) {
    throw new Error(
      `Boltz claim request failed (${claimResponse.status}): ` +
        JSON.stringify(claimResponse.data)
    );
  }

  const { pubNonce: boltzPubNonce, partialSignature: boltzPartialSig } =
    claimResponse.data;

  // ── Step 7: Musig2 signing ceremony ──
  // aggregateNonces auto-includes our nonce when given [[otherKey, otherNonce]]
  musig.aggregateNonces([
    [refundPubKey, Musig.parsePubNonce(boltzPubNonce)],
  ]);

  // Initialize signing session with the sighash
  musig.initializeSession(sigHash);

  // Create our partial signature
  musig.signPartial();

  // Verify and add Boltz's partial signature
  musig.addPartial(refundPubKey, Buffer.from(boltzPartialSig, 'hex'));

  // Aggregate into final Schnorr signature
  const finalSig = musig.aggregatePartials();

  // ── Step 8: Set the real witness and broadcast ──
  claimTx.ins[0].witness = [finalSig];

  const finalTxHex = claimTx.toHex();

  // Broadcast via Boltz API (they'll relay to the Bitcoin network)
  const broadcastResponse = await httpRequest(
    `${boltzUrl}/chain/BTC/transaction`,
    'POST',
    { hex: finalTxHex }
  );

  if (broadcastResponse.status !== 200 && broadcastResponse.status !== 201) {
    throw new Error(
      `Broadcast failed (${broadcastResponse.status}): ` +
        JSON.stringify(broadcastResponse.data)
    );
  }

  const txid =
    broadcastResponse.data.id ||
    broadcastResponse.data.txid ||
    claimTx.getId();

  // Output result as JSON on stdout
  console.log(JSON.stringify({ txid, txHex: finalTxHex }));
}

main().catch((err) => {
  process.stderr.write(
    JSON.stringify({ error: err.message, stack: err.stack }) + '\n'
  );
  process.exit(1);
});
