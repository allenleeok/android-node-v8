/* eslint node-core/documented-errors: "error" */
/* eslint node-core/alphabetize-errors: "error" */
/* eslint node-core/prefer-util-format-errors: "error" */

'use strict';

// The whole point behind this internal module is to allow Node.js to no
// longer be forced to treat every error message change as a semver-major
// change. The NodeError classes here all expose a `code` property whose
// value statically and permanently identifies the error. While the error
// message may change, the code should not.

const { Object, Math } = primordials;

const kCode = Symbol('code');
const kInfo = Symbol('info');
const messages = new Map();
const codes = {};

const { kMaxLength } = internalBinding('buffer');

const MainContextError = Error;
const ErrorToString = Error.prototype.toString;
// Polyfill of V8's Error.prepareStackTrace API.
// https://crbug.com/v8/7848
const prepareStackTrace = (globalThis, error, trace) => {
  // `globalThis` is the global that contains the constructor which
  // created `error`.
  if (typeof globalThis.Error.prepareStackTrace === 'function') {
    return globalThis.Error.prepareStackTrace(error, trace);
  }
  // We still have legacy usage that depends on the main context's `Error`
  // being used, even when the error is from a different context.
  // TODO(devsnek): evaluate if this can be eventually deprecated/removed.
  if (typeof MainContextError.prepareStackTrace === 'function') {
    return MainContextError.prepareStackTrace(error, trace);
  }

  const errorString = ErrorToString.call(error);
  if (trace.length === 0) {
    return errorString;
  }
  return `${errorString}\n    at ${trace.join('\n    at ')}`;
};


let excludedStackFn;

// Lazily loaded
let util;
let assert;

let internalUtil = null;
function lazyInternalUtil() {
  if (!internalUtil) {
    internalUtil = require('internal/util');
  }
  return internalUtil;
}

let internalUtilInspect = null;
function lazyInternalUtilInspect() {
  if (!internalUtilInspect) {
    internalUtilInspect = require('internal/util/inspect');
  }
  return internalUtilInspect;
}

let buffer;
function lazyBuffer() {
  if (buffer === undefined)
    buffer = require('buffer').Buffer;
  return buffer;
}

// A specialized Error that includes an additional info property with
// additional information about the error condition.
// It has the properties present in a UVException but with a custom error
// message followed by the uv error code and uv error message.
// It also has its own error code with the original uv error context put into
// `err.info`.
// The context passed into this error must have .code, .syscall and .message,
// and may have .path and .dest.
class SystemError extends Error {
  constructor(key, context) {
    if (excludedStackFn === undefined) {
      super();
    } else {
      const limit = Error.stackTraceLimit;
      Error.stackTraceLimit = 0;
      super();
      // Reset the limit and setting the name property.
      Error.stackTraceLimit = limit;
    }
    const prefix = getMessage(key, [], this);
    let message = `${prefix}: ${context.syscall} returned ` +
                  `${context.code} (${context.message})`;

    if (context.path !== undefined)
      message += ` ${context.path}`;
    if (context.dest !== undefined)
      message += ` => ${context.dest}`;

    Object.defineProperty(this, 'message', {
      value: message,
      enumerable: false,
      writable: true,
      configurable: true
    });
    Object.defineProperty(this, kInfo, {
      configurable: false,
      enumerable: false,
      value: context
    });
    Object.defineProperty(this, kCode, {
      configurable: true,
      enumerable: false,
      value: key,
      writable: true
    });
    addCodeToName(this, 'SystemError', key);
  }

  get code() {
    return this[kCode];
  }

  set code(value) {
    Object.defineProperty(this, 'code', {
      configurable: true,
      enumerable: true,
      value,
      writable: true
    });
  }

  get info() {
    return this[kInfo];
  }

  get errno() {
    return this[kInfo].errno;
  }

  set errno(val) {
    this[kInfo].errno = val;
  }

  get syscall() {
    return this[kInfo].syscall;
  }

  set syscall(val) {
    this[kInfo].syscall = val;
  }

  get path() {
    return this[kInfo].path !== undefined ?
      this[kInfo].path.toString() : undefined;
  }

  set path(val) {
    this[kInfo].path = val ?
      lazyBuffer().from(val.toString()) : undefined;
  }

  get dest() {
    return this[kInfo].path !== undefined ?
      this[kInfo].dest.toString() : undefined;
  }

  set dest(val) {
    this[kInfo].dest = val ?
      lazyBuffer().from(val.toString()) : undefined;
  }

  toString() {
    return `${this.name} [${this.code}]: ${this.message}`;
  }
}

function makeSystemErrorWithCode(key) {
  return class NodeError extends SystemError {
    constructor(ctx) {
      super(key, ctx);
    }
  };
}

function makeNodeErrorWithCode(Base, key) {
  return class NodeError extends Base {
    constructor(...args) {
      if (excludedStackFn === undefined) {
        super();
      } else {
        const limit = Error.stackTraceLimit;
        Error.stackTraceLimit = 0;
        super();
        // Reset the limit and setting the name property.
        Error.stackTraceLimit = limit;
      }
      const message = getMessage(key, args, this);
      Object.defineProperty(this, 'message', {
        value: message,
        enumerable: false,
        writable: true,
        configurable: true
      });
      addCodeToName(this, super.name, key);
    }

    get code() {
      return key;
    }

    set code(value) {
      Object.defineProperty(this, 'code', {
        configurable: true,
        enumerable: true,
        value,
        writable: true
      });
    }

    toString() {
      return `${this.name} [${key}]: ${this.message}`;
    }
  };
}

// This function removes unnecessary frames from Node.js core errors.
function hideStackFrames(fn) {
  return function hidden(...args) {
    // Make sure the most outer `hideStackFrames()` function is used.
    let setStackFn = false;
    if (excludedStackFn === undefined) {
      excludedStackFn = hidden;
      setStackFn = true;
    }
    try {
      return fn(...args);
    } finally {
      if (setStackFn === true) {
        excludedStackFn = undefined;
      }
    }
  };
}

function addCodeToName(err, name, code) {
  // Set the stack
  if (excludedStackFn !== undefined) {
    // eslint-disable-next-line no-restricted-syntax
    Error.captureStackTrace(err, excludedStackFn);
  }
  // Add the error code to the name to include it in the stack trace.
  err.name = `${name} [${code}]`;
  // Access the stack to generate the error message including the error code
  // from the name.
  err.stack;
  // Reset the name to the actual name.
  if (name === 'SystemError') {
    Object.defineProperty(err, 'name', {
      value: name,
      enumerable: false,
      writable: true,
      configurable: true
    });
  } else {
    delete err.name;
  }
}

// Utility function for registering the error codes. Only used here. Exported
// *only* to allow for testing.
function E(sym, val, def, ...otherClasses) {
  // Special case for SystemError that formats the error message differently
  // The SystemErrors only have SystemError as their base classes.
  messages.set(sym, val);
  if (def === SystemError) {
    def = makeSystemErrorWithCode(sym);
  } else {
    def = makeNodeErrorWithCode(def, sym);
  }

  if (otherClasses.length !== 0) {
    otherClasses.forEach((clazz) => {
      def[clazz.name] = makeNodeErrorWithCode(clazz, sym);
    });
  }
  codes[sym] = def;
}

function getMessage(key, args, self) {
  const msg = messages.get(key);

  if (assert === undefined) assert = require('internal/assert');

  if (typeof msg === 'function') {
    assert(
      msg.length <= args.length, // Default options do not count.
      `Code: ${key}; The provided arguments length (${args.length}) does not ` +
        `match the required ones (${msg.length}).`
    );
    return msg.apply(self, args);
  }

  const expectedLength = (msg.match(/%[dfijoOs]/g) || []).length;
  assert(
    expectedLength === args.length,
    `Code: ${key}; The provided arguments length (${args.length}) does not ` +
      `match the required ones (${expectedLength}).`
  );
  if (args.length === 0)
    return msg;

  args.unshift(msg);
  return lazyInternalUtilInspect().format.apply(null, args);
}

let uvBinding;

function lazyUv() {
  if (!uvBinding) {
    uvBinding = internalBinding('uv');
  }
  return uvBinding;
}

function lazyErrmapGet(name) {
  uvBinding = lazyUv();
  if (!uvBinding.errmap) {
    uvBinding.errmap = uvBinding.getErrorMap();
  }
  return uvBinding.errmap.get(name);
}


/**
 * This creates an error compatible with errors produced in the C++
 * function UVException using a context object with data assembled in C++.
 * The goal is to migrate them to ERR_* errors later when compatibility is
 * not a concern.
 *
 * @param {Object} ctx
 * @returns {Error}
 */
function uvException(ctx) {
  const [ code, uvmsg ] = lazyErrmapGet(ctx.errno);
  let message = `${code}: ${ctx.message || uvmsg}, ${ctx.syscall}`;

  let path;
  let dest;
  if (ctx.path) {
    path = ctx.path.toString();
    message += ` '${path}'`;
  }
  if (ctx.dest) {
    dest = ctx.dest.toString();
    message += ` -> '${dest}'`;
  }

  // Reducing the limit improves the performance significantly. We do not loose
  // the stack frames due to the `captureStackTrace()` function that is called
  // later.
  const tmpLimit = Error.stackTraceLimit;
  Error.stackTraceLimit = 0;
  // Pass the message to the constructor instead of setting it on the object
  // to make sure it is the same as the one created in C++
  // eslint-disable-next-line no-restricted-syntax
  const err = new Error(message);
  Error.stackTraceLimit = tmpLimit;

  for (const prop of Object.keys(ctx)) {
    if (prop === 'message' || prop === 'path' || prop === 'dest') {
      continue;
    }
    err[prop] = ctx[prop];
  }

  // TODO(BridgeAR): Show the `code` property as part of the stack.
  err.code = code;
  if (path) {
    err.path = path;
  }
  if (dest) {
    err.dest = dest;
  }

  // eslint-disable-next-line no-restricted-syntax
  Error.captureStackTrace(err, excludedStackFn || uvException);
  return err;
}

/**
 * This creates an error compatible with errors produced in the C++
 * This function should replace the deprecated
 * `exceptionWithHostPort()` function.
 *
 * @param {number} err - A libuv error number
 * @param {string} syscall
 * @param {string} address
 * @param {number} [port]
 * @returns {Error}
 */
function uvExceptionWithHostPort(err, syscall, address, port) {
  const [ code, uvmsg ] = lazyErrmapGet(err);
  const message = `${syscall} ${code}: ${uvmsg}`;
  let details = '';

  if (port && port > 0) {
    details = ` ${address}:${port}`;
  } else if (address) {
    details = ` ${address}`;
  }

  // Reducing the limit improves the performance significantly. We do not loose
  // the stack frames due to the `captureStackTrace()` function that is called
  // later.
  const tmpLimit = Error.stackTraceLimit;
  Error.stackTraceLimit = 0;
  // eslint-disable-next-line no-restricted-syntax
  const ex = new Error(`${message}${details}`);
  Error.stackTraceLimit = tmpLimit;
  ex.code = code;
  ex.errno = code;
  ex.syscall = syscall;
  ex.address = address;
  if (port) {
    ex.port = port;
  }

  // eslint-disable-next-line no-restricted-syntax
  Error.captureStackTrace(ex, excludedStackFn || uvExceptionWithHostPort);
  return ex;
}

/**
 * This used to be util._errnoException().
 *
 * @param {number} err - A libuv error number
 * @param {string} syscall
 * @param {string} [original]
 * @returns {Error}
 */
function errnoException(err, syscall, original) {
  // TODO(joyeecheung): We have to use the type-checked
  // getSystemErrorName(err) to guard against invalid arguments from users.
  // This can be replaced with [ code ] = errmap.get(err) when this method
  // is no longer exposed to user land.
  if (util === undefined) util = require('util');
  const code = util.getSystemErrorName(err);
  const message = original ?
    `${syscall} ${code} ${original}` : `${syscall} ${code}`;

  // eslint-disable-next-line no-restricted-syntax
  const ex = new Error(message);
  // TODO(joyeecheung): errno is supposed to err, like in uvException
  ex.code = ex.errno = code;
  ex.syscall = syscall;

  // eslint-disable-next-line no-restricted-syntax
  Error.captureStackTrace(ex, excludedStackFn || errnoException);
  return ex;
}

/**
 * Deprecated, new function is `uvExceptionWithHostPort()`
 * New function added the error description directly
 * from C++. this method for backwards compatibility
 * @param {number} err - A libuv error number
 * @param {string} syscall
 * @param {string} address
 * @param {number} [port]
 * @param {string} [additional]
 * @returns {Error}
 */
function exceptionWithHostPort(err, syscall, address, port, additional) {
  // TODO(joyeecheung): We have to use the type-checked
  // getSystemErrorName(err) to guard against invalid arguments from users.
  // This can be replaced with [ code ] = errmap.get(err) when this method
  // is no longer exposed to user land.
  if (util === undefined) util = require('util');
  const code = util.getSystemErrorName(err);
  let details = '';
  if (port && port > 0) {
    details = ` ${address}:${port}`;
  } else if (address) {
    details = ` ${address}`;
  }
  if (additional) {
    details += ` - Local (${additional})`;
  }

  // Reducing the limit improves the performance significantly. We do not loose
  // the stack frames due to the `captureStackTrace()` function that is called
  // later.
  const tmpLimit = Error.stackTraceLimit;
  Error.stackTraceLimit = 0;
  // eslint-disable-next-line no-restricted-syntax
  const ex = new Error(`${syscall} ${code}${details}`);
  // TODO(joyeecheung): errno is supposed to err, like in uvException
  Error.stackTraceLimit = tmpLimit;
  ex.code = ex.errno = code;
  ex.syscall = syscall;
  ex.address = address;
  if (port) {
    ex.port = port;
  }

  // eslint-disable-next-line no-restricted-syntax
  Error.captureStackTrace(ex, excludedStackFn || exceptionWithHostPort);
  return ex;
}

/**
 * @param {number|string} code - A libuv error number or a c-ares error code
 * @param {string} syscall
 * @param {string} [hostname]
 * @returns {Error}
 */
function dnsException(code, syscall, hostname) {
  // If `code` is of type number, it is a libuv error number, else it is a
  // c-ares error code.
  if (typeof code === 'number') {
    // ENOTFOUND is not a proper POSIX error, but this error has been in place
    // long enough that it's not practical to remove it.
    if (code === lazyUv().UV_EAI_NODATA || code === lazyUv().UV_EAI_NONAME) {
      code = 'ENOTFOUND'; // Fabricated error name.
    } else {
      code = lazyInternalUtil().getSystemErrorName(code);
    }
  }
  const message = `${syscall} ${code}${hostname ? ` ${hostname}` : ''}`;
  // Reducing the limit improves the performance significantly. We do not loose
  // the stack frames due to the `captureStackTrace()` function that is called
  // later.
  const tmpLimit = Error.stackTraceLimit;
  Error.stackTraceLimit = 0;
  // eslint-disable-next-line no-restricted-syntax
  const ex = new Error(message);
  // TODO(joyeecheung): errno is supposed to be a number / err, like in
  Error.stackTraceLimit = tmpLimit;
  // uvException.
  ex.errno = code;
  ex.code = code;
  ex.syscall = syscall;
  if (hostname) {
    ex.hostname = hostname;
  }

  // eslint-disable-next-line no-restricted-syntax
  Error.captureStackTrace(ex, excludedStackFn || dnsException);
  return ex;
}

function connResetException(msg) {
  // eslint-disable-next-line no-restricted-syntax
  const ex = new Error(msg);
  ex.code = 'ECONNRESET';
  return ex;
}

let maxStack_ErrorName;
let maxStack_ErrorMessage;
/**
 * Returns true if `err.name` and `err.message` are equal to engine-specific
 * values indicating max call stack size has been exceeded.
 * "Maximum call stack size exceeded" in V8.
 *
 * @param {Error} err
 * @returns {boolean}
 */
function isStackOverflowError(err) {
  if (maxStack_ErrorMessage === undefined) {
    try {
      function overflowStack() { overflowStack(); }
      overflowStack();
    } catch (err) {
      maxStack_ErrorMessage = err.message;
      maxStack_ErrorName = err.name;
    }
  }

  return err && err.name === maxStack_ErrorName &&
         err.message === maxStack_ErrorMessage;
}

function oneOf(expected, thing) {
  assert(typeof thing === 'string', '`thing` has to be of type string');
  if (Array.isArray(expected)) {
    const len = expected.length;
    assert(len > 0,
           'At least one expected value needs to be specified');
    expected = expected.map((i) => String(i));
    if (len > 2) {
      return `one of ${thing} ${expected.slice(0, len - 1).join(', ')}, or ` +
             expected[len - 1];
    } else if (len === 2) {
      return `one of ${thing} ${expected[0]} or ${expected[1]}`;
    } else {
      return `of ${thing} ${expected[0]}`;
    }
  } else {
    return `of ${thing} ${String(expected)}`;
  }
}

// Only use this for integers! Decimal numbers do not work with this function.
function addNumericalSeparator(val) {
  let res = '';
  let i = val.length;
  const start = val[0] === '-' ? 1 : 0;
  for (; i >= start + 4; i -= 3) {
    res = `_${val.slice(i - 3, i)}${res}`;
  }
  return `${val.slice(0, i)}${res}`;
}

// Used to enhance the stack that will be picked up by the inspector
const kEnhanceStackBeforeInspector = Symbol('kEnhanceStackBeforeInspector');

// These are supposed to be called only on fatal exceptions before
// the process exits.
const fatalExceptionStackEnhancers = {
  beforeInspector(error) {
    if (typeof error[kEnhanceStackBeforeInspector] !== 'function') {
      return error.stack;
    }

    try {
      // Set the error.stack here so it gets picked up by the
      // inspector.
      error.stack = error[kEnhanceStackBeforeInspector]();
    } catch {
      // We are just enhancing the error. If it fails, ignore it.
    }
    return error.stack;
  },
  afterInspector(error) {
    const originalStack = error.stack;
    const {
      inspect,
      inspectDefaultOptions: {
        colors: defaultColors
      }
    } = lazyInternalUtilInspect();
    const colors = internalBinding('util').guessHandleType(2) === 'TTY' &&
                   require('internal/tty').hasColors() ||
                   defaultColors;
    try {
      return inspect(error, { colors });
    } catch {
      return originalStack;
    }
  }
};

module.exports = {
  addCodeToName, // Exported for NghttpError
  codes,
  dnsException,
  errnoException,
  exceptionWithHostPort,
  getMessage,
  hideStackFrames,
  isStackOverflowError,
  connResetException,
  uvException,
  uvExceptionWithHostPort,
  SystemError,
  // This is exported only to facilitate testing.
  E,
  prepareStackTrace,
  kEnhanceStackBeforeInspector,
  fatalExceptionStackEnhancers
};

// To declare an error message, use the E(sym, val, def) function above. The sym
// must be an upper case string. The val can be either a function or a string.
// The def must be an error class.
// The return value of the function must be a string.
// Examples:
// E('EXAMPLE_KEY1', 'This is the error value', Error);
// E('EXAMPLE_KEY2', (a, b) => return `${a} ${b}`, RangeError);
//
// Once an error code has been assigned, the code itself MUST NOT change and
// any given error code must never be reused to identify a different error.
//
// Any error code added here should also be added to the documentation
//
// Note: Please try to keep these in alphabetical order
//
// Note: Node.js specific errors must begin with the prefix ERR_
E('ERR_AMBIGUOUS_ARGUMENT', 'The "%s" argument is ambiguous. %s', TypeError);
E('ERR_ARG_NOT_ITERABLE', '%s must be iterable', TypeError);
E('ERR_ASSERTION', '%s', Error);
E('ERR_ASYNC_CALLBACK', '%s must be a function', TypeError);
E('ERR_ASYNC_TYPE', 'Invalid name for async "type": %s', TypeError);
E('ERR_BROTLI_INVALID_PARAM', '%s is not a valid Brotli parameter', RangeError);
E('ERR_BUFFER_OUT_OF_BOUNDS',
  // Using a default argument here is important so the argument is not counted
  // towards `Function#length`.
  (name = undefined) => {
    if (name) {
      return `"${name}" is outside of buffer bounds`;
    }
    return 'Attempt to write outside buffer bounds';
  }, RangeError);
E('ERR_BUFFER_TOO_LARGE',
  `Cannot create a Buffer larger than 0x${kMaxLength.toString(16)} bytes`,
  RangeError);
E('ERR_CANNOT_WATCH_SIGINT', 'Cannot watch for SIGINT signals', Error);
E('ERR_CHILD_CLOSED_BEFORE_REPLY',
  'Child closed before reply received', Error);
E('ERR_CHILD_PROCESS_IPC_REQUIRED',
  "Forked processes must have an IPC channel, missing value 'ipc' in %s",
  Error);
E('ERR_CHILD_PROCESS_STDIO_MAXBUFFER', '%s maxBuffer length exceeded',
  RangeError);
E('ERR_CONSOLE_WRITABLE_STREAM',
  'Console expects a writable stream instance for %s', TypeError);
E('ERR_CPU_USAGE', 'Unable to obtain cpu usage %s', Error);
E('ERR_CRYPTO_CUSTOM_ENGINE_NOT_SUPPORTED',
  'Custom engines not supported by this OpenSSL', Error);
E('ERR_CRYPTO_ECDH_INVALID_FORMAT', 'Invalid ECDH format: %s', TypeError);
E('ERR_CRYPTO_ECDH_INVALID_PUBLIC_KEY',
  'Public key is not valid for specified curve', Error);
E('ERR_CRYPTO_ENGINE_UNKNOWN', 'Engine "%s" was not found', Error);
E('ERR_CRYPTO_FIPS_FORCED',
  'Cannot set FIPS mode, it was forced with --force-fips at startup.', Error);
E('ERR_CRYPTO_FIPS_UNAVAILABLE', 'Cannot set FIPS mode in a non-FIPS build.',
  Error);
E('ERR_CRYPTO_HASH_DIGEST_NO_UTF16', 'hash.digest() does not support UTF-16',
  Error);
E('ERR_CRYPTO_HASH_FINALIZED', 'Digest already called', Error);
E('ERR_CRYPTO_HASH_UPDATE_FAILED', 'Hash update failed', Error);
E('ERR_CRYPTO_INCOMPATIBLE_KEY_OPTIONS', 'The selected key encoding %s %s.',
  Error);
E('ERR_CRYPTO_INVALID_DIGEST', 'Invalid digest: %s', TypeError);
E('ERR_CRYPTO_INVALID_KEY_OBJECT_TYPE',
  'Invalid key object type %s, expected %s.', TypeError);
E('ERR_CRYPTO_INVALID_STATE', 'Invalid state for operation %s', Error);
E('ERR_CRYPTO_PBKDF2_ERROR', 'PBKDF2 error', Error);
E('ERR_CRYPTO_SCRYPT_INVALID_PARAMETER', 'Invalid scrypt parameter', Error);
E('ERR_CRYPTO_SCRYPT_NOT_SUPPORTED', 'Scrypt algorithm not supported', Error);
// Switch to TypeError. The current implementation does not seem right.
E('ERR_CRYPTO_SIGN_KEY_REQUIRED', 'No key provided to sign', Error);
E('ERR_CRYPTO_TIMING_SAFE_EQUAL_LENGTH',
  'Input buffers must have the same length', RangeError);
E('ERR_DNS_SET_SERVERS_FAILED', 'c-ares failed to set servers: "%s" [%s]',
  Error);
E('ERR_DOMAIN_CALLBACK_NOT_AVAILABLE',
  'A callback was registered through ' +
     'process.setUncaughtExceptionCaptureCallback(), which is mutually ' +
     'exclusive with using the `domain` module',
  Error);
E('ERR_DOMAIN_CANNOT_SET_UNCAUGHT_EXCEPTION_CAPTURE',
  'The `domain` module is in use, which is mutually exclusive with calling ' +
     'process.setUncaughtExceptionCaptureCallback()',
  Error);
E('ERR_ENCODING_INVALID_ENCODED_DATA', function(encoding, ret) {
  this.errno = ret;
  return `The encoded data was not valid for encoding ${encoding}`;
}, TypeError);
E('ERR_ENCODING_NOT_SUPPORTED', 'The "%s" encoding is not supported',
  RangeError);
E('ERR_FALSY_VALUE_REJECTION', function(reason) {
  this.reason = reason;
  return 'Promise was rejected with falsy value';
}, Error);
E('ERR_FS_FILE_TOO_LARGE', 'File size (%s) is greater than possible Buffer: ' +
    `${kMaxLength} bytes`,
  RangeError);
E('ERR_FS_INVALID_SYMLINK_TYPE',
  'Symlink type must be one of "dir", "file", or "junction". Received "%s"',
  Error); // Switch to TypeError. The current implementation does not seem right
E('ERR_HTTP2_ALTSVC_INVALID_ORIGIN',
  'HTTP/2 ALTSVC frames require a valid origin', TypeError);
E('ERR_HTTP2_ALTSVC_LENGTH',
  'HTTP/2 ALTSVC frames are limited to 16382 bytes', TypeError);
E('ERR_HTTP2_CONNECT_AUTHORITY',
  ':authority header is required for CONNECT requests', Error);
E('ERR_HTTP2_CONNECT_PATH',
  'The :path header is forbidden for CONNECT requests', Error);
E('ERR_HTTP2_CONNECT_SCHEME',
  'The :scheme header is forbidden for CONNECT requests', Error);
E('ERR_HTTP2_GOAWAY_SESSION',
  'New streams cannot be created after receiving a GOAWAY', Error);
E('ERR_HTTP2_HEADERS_AFTER_RESPOND',
  'Cannot specify additional headers after response initiated', Error);
E('ERR_HTTP2_HEADERS_SENT', 'Response has already been initiated.', Error);
E('ERR_HTTP2_HEADER_SINGLE_VALUE',
  'Header field "%s" must only have a single value', TypeError);
E('ERR_HTTP2_INFO_STATUS_NOT_ALLOWED',
  'Informational status codes cannot be used', RangeError);
E('ERR_HTTP2_INVALID_CONNECTION_HEADERS',
  'HTTP/1 Connection specific headers are forbidden: "%s"', TypeError);
E('ERR_HTTP2_INVALID_HEADER_VALUE',
  'Invalid value "%s" for header "%s"', TypeError);
E('ERR_HTTP2_INVALID_INFO_STATUS',
  'Invalid informational status code: %s', RangeError);
E('ERR_HTTP2_INVALID_ORIGIN',
  'HTTP/2 ORIGIN frames require a valid origin', TypeError);
E('ERR_HTTP2_INVALID_PACKED_SETTINGS_LENGTH',
  'Packed settings length must be a multiple of six', RangeError);
E('ERR_HTTP2_INVALID_PSEUDOHEADER',
  '"%s" is an invalid pseudoheader or is used incorrectly', TypeError);
E('ERR_HTTP2_INVALID_SESSION', 'The session has been destroyed', Error);
E('ERR_HTTP2_INVALID_SETTING_VALUE',
  // Using default arguments here is important so the arguments are not counted
  // towards `Function#length`.
  function(name, actual, min = undefined, max = undefined) {
    this.actual = actual;
    if (min !== undefined) {
      this.min = min;
      this.max = max;
    }
    return `Invalid value for setting "${name}": ${actual}`;
  }, TypeError, RangeError);
E('ERR_HTTP2_INVALID_STREAM', 'The stream has been destroyed', Error);
E('ERR_HTTP2_MAX_PENDING_SETTINGS_ACK',
  'Maximum number of pending settings acknowledgements', Error);
E('ERR_HTTP2_NESTED_PUSH',
  'A push stream cannot initiate another push stream.', Error);
E('ERR_HTTP2_NO_SOCKET_MANIPULATION',
  'HTTP/2 sockets should not be directly manipulated (e.g. read and written)',
  Error);
E('ERR_HTTP2_ORIGIN_LENGTH',
  'HTTP/2 ORIGIN frames are limited to 16382 bytes', TypeError);
E('ERR_HTTP2_OUT_OF_STREAMS',
  'No stream ID is available because maximum stream ID has been reached',
  Error);
E('ERR_HTTP2_PAYLOAD_FORBIDDEN',
  'Responses with %s status must not have a payload', Error);
E('ERR_HTTP2_PING_CANCEL', 'HTTP2 ping cancelled', Error);
E('ERR_HTTP2_PING_LENGTH', 'HTTP2 ping payload must be 8 bytes', RangeError);
E('ERR_HTTP2_PSEUDOHEADER_NOT_ALLOWED',
  'Cannot set HTTP/2 pseudo-headers', TypeError);
E('ERR_HTTP2_PUSH_DISABLED', 'HTTP/2 client has disabled push streams', Error);
E('ERR_HTTP2_SEND_FILE', 'Directories cannot be sent', Error);
E('ERR_HTTP2_SEND_FILE_NOSEEK',
  'Offset or length can only be specified for regular files', Error);
E('ERR_HTTP2_SESSION_ERROR', 'Session closed with error code %s', Error);
E('ERR_HTTP2_SETTINGS_CANCEL', 'HTTP2 session settings canceled', Error);
E('ERR_HTTP2_SOCKET_BOUND',
  'The socket is already bound to an Http2Session', Error);
E('ERR_HTTP2_SOCKET_UNBOUND',
  'The socket has been disconnected from the Http2Session', Error);
E('ERR_HTTP2_STATUS_101',
  'HTTP status code 101 (Switching Protocols) is forbidden in HTTP/2', Error);
E('ERR_HTTP2_STATUS_INVALID', 'Invalid status code: %s', RangeError);
E('ERR_HTTP2_STREAM_CANCEL', function(error) {
  let msg = 'The pending stream has been canceled';
  if (error) {
    this.cause = error;
    if (typeof error.message === 'string')
      msg += ` (caused by: ${error.message})`;
  }
  return msg;
}, Error);
E('ERR_HTTP2_STREAM_ERROR', 'Stream closed with error code %s', Error);
E('ERR_HTTP2_STREAM_SELF_DEPENDENCY',
  'A stream cannot depend on itself', Error);
E('ERR_HTTP2_TRAILERS_ALREADY_SENT',
  'Trailing headers have already been sent', Error);
E('ERR_HTTP2_TRAILERS_NOT_READY',
  'Trailing headers cannot be sent until after the wantTrailers event is ' +
  'emitted', Error);
E('ERR_HTTP2_UNSUPPORTED_PROTOCOL', 'protocol "%s" is unsupported.', Error);
E('ERR_HTTP_HEADERS_SENT',
  'Cannot %s headers after they are sent to the client', Error);
E('ERR_HTTP_INVALID_HEADER_VALUE',
  'Invalid value "%s" for header "%s"', TypeError);
E('ERR_HTTP_INVALID_STATUS_CODE', 'Invalid status code: %s', RangeError);
E('ERR_HTTP_TRAILER_INVALID',
  'Trailers are invalid with this transfer encoding', Error);
E('ERR_INCOMPATIBLE_OPTION_PAIR',
  'Option "%s" can not be used in combination with option "%s"', TypeError);
E('ERR_INPUT_TYPE_NOT_ALLOWED', '--input-type can only be used with string ' +
  'input via --eval, --print, or STDIN', Error);
E('ERR_INSPECTOR_ALREADY_CONNECTED', '%s is already connected', Error);
E('ERR_INSPECTOR_CLOSED', 'Session was closed', Error);
E('ERR_INSPECTOR_COMMAND', 'Inspector error %d: %s', Error);
E('ERR_INSPECTOR_NOT_ACTIVE', 'Inspector is not active', Error);
E('ERR_INSPECTOR_NOT_AVAILABLE', 'Inspector is not available', Error);
E('ERR_INSPECTOR_NOT_CONNECTED', 'Session is not connected', Error);
E('ERR_INTERNAL_ASSERTION', (message) => {
  const suffix = 'This is caused by either a bug in Node.js ' +
    'or incorrect usage of Node.js internals.\n' +
    'Please open an issue with this stack trace at ' +
    'https://github.com/nodejs/node/issues\n';
  return message === undefined ? suffix : `${message}\n${suffix}`;
}, Error);
E('ERR_INVALID_ADDRESS_FAMILY', function(addressType, host, port) {
  this.host = host;
  this.port = port;
  return `Invalid address family: ${addressType} ${host}:${port}`;
}, RangeError);
E('ERR_INVALID_ARG_TYPE',
  (name, expected, actual) => {
    assert(typeof name === 'string', "'name' must be a string");

    // determiner: 'must be' or 'must not be'
    let determiner;
    if (typeof expected === 'string' && expected.startsWith('not ')) {
      determiner = 'must not be';
      expected = expected.replace(/^not /, '');
    } else {
      determiner = 'must be';
    }

    let msg;
    if (name.endsWith(' argument')) {
      // For cases like 'first argument'
      msg = `The ${name} ${determiner} ${oneOf(expected, 'type')}`;
    } else {
      const type = name.includes('.') ? 'property' : 'argument';
      msg = `The "${name}" ${type} ${determiner} ${oneOf(expected, 'type')}`;
    }

    // TODO(BridgeAR): Improve the output by showing `null` and similar.
    msg += `. Received type ${typeof actual}`;
    return msg;
  }, TypeError);
E('ERR_INVALID_ARG_VALUE', (name, value, reason = 'is invalid') => {
  let inspected = lazyInternalUtilInspect().inspect(value);
  if (inspected.length > 128) {
    inspected = `${inspected.slice(0, 128)}...`;
  }
  return `The argument '${name}' ${reason}. Received ${inspected}`;
}, TypeError, RangeError);
E('ERR_INVALID_ASYNC_ID', 'Invalid %s value: %s', RangeError);
E('ERR_INVALID_BUFFER_SIZE',
  'Buffer size must be a multiple of %s', RangeError);
E('ERR_INVALID_CALLBACK',
  'Callback must be a function. Received %O', TypeError);
E('ERR_INVALID_CHAR',
  // Using a default argument here is important so the argument is not counted
  // towards `Function#length`.
  (name, field = undefined) => {
    let msg = `Invalid character in ${name}`;
    if (field !== undefined) {
      msg += ` ["${field}"]`;
    }
    return msg;
  }, TypeError);
E('ERR_INVALID_CURSOR_POS',
  'Cannot set cursor row without setting its column', TypeError);
E('ERR_INVALID_FD',
  '"fd" must be a positive integer: %s', RangeError);
E('ERR_INVALID_FD_TYPE', 'Unsupported fd type: %s', TypeError);
E('ERR_INVALID_FILE_URL_HOST',
  'File URL host must be "localhost" or empty on %s', TypeError);
E('ERR_INVALID_FILE_URL_PATH', 'File URL path %s', TypeError);
E('ERR_INVALID_HANDLE_TYPE', 'This handle type cannot be sent', TypeError);
E('ERR_INVALID_HTTP_TOKEN', '%s must be a valid HTTP token ["%s"]', TypeError);
E('ERR_INVALID_IP_ADDRESS', 'Invalid IP address: %s', TypeError);
E('ERR_INVALID_OPT_VALUE', (name, value) =>
  `The value "${String(value)}" is invalid for option "${name}"`,
  TypeError,
  RangeError);
E('ERR_INVALID_OPT_VALUE_ENCODING',
  'The value "%s" is invalid for option "encoding"', TypeError);
E('ERR_INVALID_PACKAGE_CONFIG',
  'Invalid package config in \'%s\' imported from %s', Error);
E('ERR_INVALID_PERFORMANCE_MARK',
  'The "%s" performance mark has not been set', Error);
E('ERR_INVALID_PROTOCOL',
  'Protocol "%s" not supported. Expected "%s"',
  TypeError);
E('ERR_INVALID_REPL_EVAL_CONFIG',
  'Cannot specify both "breakEvalOnSigint" and "eval" for REPL', TypeError);
E('ERR_INVALID_REPL_INPUT', '%s', TypeError);
E('ERR_INVALID_RETURN_PROPERTY', (input, name, prop, value) => {
  return `Expected a valid ${input} to be returned for the "${prop}" from the` +
         ` "${name}" function but got ${value}.`;
}, TypeError);
E('ERR_INVALID_RETURN_PROPERTY_VALUE', (input, name, prop, value) => {
  let type;
  if (value && value.constructor && value.constructor.name) {
    type = `instance of ${value.constructor.name}`;
  } else {
    type = `type ${typeof value}`;
  }
  return `Expected ${input} to be returned for the "${prop}" from the` +
         ` "${name}" function but got ${type}.`;
}, TypeError);
E('ERR_INVALID_RETURN_VALUE', (input, name, value) => {
  let type;
  if (value && value.constructor && value.constructor.name) {
    type = `instance of ${value.constructor.name}`;
  } else {
    type = `type ${typeof value}`;
  }
  return `Expected ${input} to be returned from the "${name}"` +
         ` function but got ${type}.`;
}, TypeError);
E('ERR_INVALID_SYNC_FORK_INPUT',
  'Asynchronous forks do not support ' +
    'Buffer, TypedArray, DataView or string input: %s',
  TypeError);
E('ERR_INVALID_THIS', 'Value of "this" must be of type %s', TypeError);
E('ERR_INVALID_TUPLE', '%s must be an iterable %s tuple', TypeError);
E('ERR_INVALID_URI', 'URI malformed', URIError);
E('ERR_INVALID_URL', function(input) {
  this.input = input;
  return `Invalid URL: ${input}`;
}, TypeError);
E('ERR_INVALID_URL_SCHEME',
  (expected) => `The URL must be ${oneOf(expected, 'scheme')}`, TypeError);
E('ERR_IPC_CHANNEL_CLOSED', 'Channel closed', Error);
E('ERR_IPC_DISCONNECTED', 'IPC channel is already disconnected', Error);
E('ERR_IPC_ONE_PIPE', 'Child process can have only one IPC pipe', Error);
E('ERR_IPC_SYNC_FORK', 'IPC cannot be used with synchronous forks', Error);
E('ERR_MANIFEST_ASSERT_INTEGRITY',
  (moduleURL, realIntegrities) => {
    let msg = `The content of "${
      moduleURL
    }" does not match the expected integrity.`;
    if (realIntegrities.size) {
      const sri = [...realIntegrities.entries()].map(([alg, dgs]) => {
        return `${alg}-${dgs}`;
      }).join(' ');
      msg += ` Integrities found are: ${sri}`;
    } else {
      msg += ' The resource was not found in the policy.';
    }
    return msg;
  }, Error);
E('ERR_MANIFEST_INTEGRITY_MISMATCH',
  'Manifest resource %s has multiple entries but integrity lists do not match',
  SyntaxError);
E('ERR_MANIFEST_TDZ', 'Manifest initialization has not yet run', Error);
E('ERR_MANIFEST_UNKNOWN_ONERROR',
  'Manifest specified unknown error behavior "%s".',
  SyntaxError);
E('ERR_METHOD_NOT_IMPLEMENTED', 'The %s method is not implemented', Error);
E('ERR_MISSING_ARGS',
  (...args) => {
    assert(args.length > 0, 'At least one arg needs to be specified');
    let msg = 'The ';
    const len = args.length;
    args = args.map((a) => `"${a}"`);
    switch (len) {
      case 1:
        msg += `${args[0]} argument`;
        break;
      case 2:
        msg += `${args[0]} and ${args[1]} arguments`;
        break;
      default:
        msg += args.slice(0, len - 1).join(', ');
        msg += `, and ${args[len - 1]} arguments`;
        break;
    }
    return `${msg} must be specified`;
  }, TypeError);
E('ERR_MISSING_DYNAMIC_INSTANTIATE_HOOK',
  'The ES Module loader may not return a format of \'dynamic\' when no ' +
  'dynamicInstantiate function was provided', Error);
E('ERR_MULTIPLE_CALLBACK', 'Callback called multiple times', Error);
E('ERR_NAPI_CONS_FUNCTION', 'Constructor must be a function', TypeError);
E('ERR_NAPI_INVALID_DATAVIEW_ARGS',
  'byte_offset + byte_length should be less than or equal to the size in ' +
    'bytes of the array passed in',
  RangeError);
E('ERR_NAPI_INVALID_TYPEDARRAY_ALIGNMENT',
  'start offset of %s should be a multiple of %s', RangeError);
E('ERR_NAPI_INVALID_TYPEDARRAY_LENGTH',
  'Invalid typed array length', RangeError);
E('ERR_NO_CRYPTO',
  'Node.js is not compiled with OpenSSL crypto support', Error);
E('ERR_NO_ICU',
  '%s is not supported on Node.js compiled without ICU', TypeError);
E('ERR_OUT_OF_RANGE',
  (str, range, input, replaceDefaultBoolean = false) => {
    assert(range, 'Missing "range" argument');
    let msg = replaceDefaultBoolean ? str :
      `The value of "${str}" is out of range.`;
    let received;
    if (Number.isInteger(input) && Math.abs(input) > 2 ** 32) {
      received = addNumericalSeparator(String(input));
    } else if (typeof input === 'bigint') {
      received = String(input);
      if (input > 2n ** 32n || input < -(2n ** 32n)) {
        received = addNumericalSeparator(received);
      }
      received += 'n';
    } else {
      received = lazyInternalUtilInspect().inspect(input);
    }
    msg += ` It must be ${range}. Received ${received}`;
    return msg;
  }, RangeError);
E('ERR_REQUIRE_ESM', 'Must use import to load ES Module: %s', Error);
E('ERR_SCRIPT_EXECUTION_INTERRUPTED',
  'Script execution was interrupted by `SIGINT`', Error);
E('ERR_SERVER_ALREADY_LISTEN',
  'Listen method has been called more than once without closing.', Error);
E('ERR_SERVER_NOT_RUNNING', 'Server is not running.', Error);
E('ERR_SOCKET_ALREADY_BOUND', 'Socket is already bound', Error);
E('ERR_SOCKET_BAD_BUFFER_SIZE',
  'Buffer size must be a positive integer', TypeError);
E('ERR_SOCKET_BAD_PORT',
  'Port should be >= 0 and < 65536. Received %s.', RangeError);
E('ERR_SOCKET_BAD_TYPE',
  'Bad socket type specified. Valid types are: udp4, udp6', TypeError);
E('ERR_SOCKET_BUFFER_SIZE',
  'Could not get or set buffer size',
  SystemError);
E('ERR_SOCKET_CANNOT_SEND', 'Unable to send data', Error);
E('ERR_SOCKET_CLOSED', 'Socket is closed', Error);
E('ERR_SOCKET_DGRAM_IS_CONNECTED', 'Already connected', Error);
E('ERR_SOCKET_DGRAM_NOT_CONNECTED', 'Not connected', Error);
E('ERR_SOCKET_DGRAM_NOT_RUNNING', 'Not running', Error);
E('ERR_SRI_PARSE',
  'Subresource Integrity string %s had an unexpected at %d',
  SyntaxError);
E('ERR_STREAM_CANNOT_PIPE', 'Cannot pipe, not readable', Error);
E('ERR_STREAM_DESTROYED', 'Cannot call %s after a stream was destroyed', Error);
E('ERR_STREAM_NULL_VALUES', 'May not write null values to stream', TypeError);
E('ERR_STREAM_PREMATURE_CLOSE', 'Premature close', Error);
E('ERR_STREAM_PUSH_AFTER_EOF', 'stream.push() after EOF', Error);
E('ERR_STREAM_UNSHIFT_AFTER_END_EVENT',
  'stream.unshift() after end event', Error);
E('ERR_STREAM_WRAP', 'Stream has StringDecoder set or is in objectMode', Error);
E('ERR_STREAM_WRITE_AFTER_END', 'write after end', Error);
E('ERR_SYNTHETIC', 'JavaScript Callstack', Error);
E('ERR_SYSTEM_ERROR', 'A system error occurred', SystemError);
E('ERR_TLS_CERT_ALTNAME_INVALID', function(reason, host, cert) {
  this.reason = reason;
  this.host = host;
  this.cert = cert;
  return `Hostname/IP does not match certificate's altnames: ${reason}`;
}, Error);
E('ERR_TLS_DH_PARAM_SIZE', 'DH parameter size %s is less than 2048', Error);
E('ERR_TLS_HANDSHAKE_TIMEOUT', 'TLS handshake timeout', Error);
E('ERR_TLS_INVALID_PROTOCOL_VERSION',
  '%j is not a valid %s TLS protocol version', TypeError);
E('ERR_TLS_PROTOCOL_VERSION_CONFLICT',
  'TLS protocol version %j conflicts with secureProtocol %j', TypeError);
E('ERR_TLS_RENEGOTIATION_DISABLED',
  'TLS session renegotiation disabled for this socket', Error);

// This should probably be a `TypeError`.
E('ERR_TLS_REQUIRED_SERVER_NAME',
  '"servername" is required parameter for Server.addContext', Error);
E('ERR_TLS_SESSION_ATTACK', 'TLS session renegotiation attack detected', Error);
E('ERR_TLS_SNI_FROM_SERVER',
  'Cannot issue SNI from a TLS server-side socket', Error);
E('ERR_TRACE_EVENTS_CATEGORY_REQUIRED',
  'At least one category is required', TypeError);
E('ERR_TRACE_EVENTS_UNAVAILABLE', 'Trace events are unavailable', Error);
E('ERR_TRANSFORM_ALREADY_TRANSFORMING',
  'Calling transform done when still transforming', Error);

// This should probably be a `RangeError`.
E('ERR_TRANSFORM_WITH_LENGTH_0',
  'Calling transform done when writableState.length != 0', Error);
E('ERR_TTY_INIT_FAILED', 'TTY initialization failed', SystemError);
E('ERR_UNCAUGHT_EXCEPTION_CAPTURE_ALREADY_SET',
  '`process.setupUncaughtExceptionCapture()` was called while a capture ' +
    'callback was already active',
  Error);
E('ERR_UNESCAPED_CHARACTERS', '%s contains unescaped characters', TypeError);
E('ERR_UNHANDLED_ERROR',
  // Using a default argument here is important so the argument is not counted
  // towards `Function#length`.
  (err = undefined) => {
    const msg = 'Unhandled error.';
    if (err === undefined) return msg;
    return `${msg} (${err})`;
  }, Error);
E('ERR_UNKNOWN_BUILTIN_MODULE', 'No such built-in module: %s', Error);
E('ERR_UNKNOWN_CREDENTIAL', '%s identifier does not exist: %s', Error);
E('ERR_UNKNOWN_ENCODING', 'Unknown encoding: %s', TypeError);
E('ERR_UNKNOWN_FILE_EXTENSION', 'Unknown file extension: %s', TypeError);
E('ERR_UNKNOWN_MODULE_FORMAT', 'Unknown module format: %s', RangeError);
E('ERR_UNKNOWN_SIGNAL', 'Unknown signal: %s', TypeError);

E('ERR_V8BREAKITERATOR',
  'Full ICU data not installed. See https://github.com/nodejs/node/wiki/Intl',
  Error);

// This should probably be a `TypeError`.
E('ERR_VALID_PERFORMANCE_ENTRY_TYPE',
  'At least one valid performance entry type is required', Error);
E('ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING',
  'A dynamic import callback was not specified.', TypeError);
E('ERR_VM_MODULE_ALREADY_LINKED', 'Module has already been linked', Error);
E('ERR_VM_MODULE_DIFFERENT_CONTEXT',
  'Linked modules must use the same context', Error);
E('ERR_VM_MODULE_LINKING_ERRORED',
  'Linking has already failed for the provided module', Error);
E('ERR_VM_MODULE_NOT_LINKED',
  'Module must be linked before it can be instantiated', Error);
E('ERR_VM_MODULE_NOT_MODULE',
  'Provided module is not an instance of Module', Error);
E('ERR_VM_MODULE_STATUS', 'Module status %s', Error);
E('ERR_WORKER_INVALID_EXEC_ARGV', (errors) =>
  `Initiated Worker with invalid execArgv flags: ${errors.join(', ')}`,
  Error);
E('ERR_WORKER_PATH',
  'The worker script filename must be an absolute path or a relative ' +
  'path starting with \'./\' or \'../\'. Received "%s"',
  TypeError);
E('ERR_WORKER_UNSERIALIZABLE_ERROR',
  'Serializing an uncaught exception failed', Error);
E('ERR_WORKER_UNSUPPORTED_EXTENSION',
  'The worker script extension must be ".js" or ".mjs". Received "%s"',
  TypeError);
E('ERR_WORKER_UNSUPPORTED_OPERATION',
  '%s is not supported in workers', TypeError);
E('ERR_ZLIB_INITIALIZATION_FAILED', 'Initialization failed', Error);
