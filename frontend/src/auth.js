/**
 * Sign-in, loaded only if the server asks for it.
 *
 * The server decides: `/api/config` reports whether auth is required, and
 * the Firebase SDK is imported lazily so an open demo never pays for a
 * dependency it does not use. One build therefore serves both an
 * unauthenticated local run and a protected deployment.
 */

let token = null
let firebaseAuth = null

export function getToken() {
  return token
}

export function setToken(value) {
  token = value
}

/** Load Firebase and attach a listener. Returns an unsubscribe function. */
export async function initFirebase(config, onUser) {
  const [{ initializeApp }, authMod] = await Promise.all([
    import('firebase/app'),
    import('firebase/auth'),
  ])
  const app = initializeApp(config)
  firebaseAuth = authMod.getAuth(app)

  return authMod.onIdTokenChanged(firebaseAuth, async (user) => {
    token = user ? await user.getIdToken() : null
    onUser(user ? { email: user.email, uid: user.uid } : null)
  })
}

export async function signIn() {
  if (!firebaseAuth) throw new Error('sign-in is not configured')
  const { GoogleAuthProvider, signInWithPopup } = await import('firebase/auth')
  await signInWithPopup(firebaseAuth, new GoogleAuthProvider())
}

export async function signOut() {
  if (!firebaseAuth) return
  const { signOut: fbSignOut } = await import('firebase/auth')
  await fbSignOut(firebaseAuth)
  token = null
}
