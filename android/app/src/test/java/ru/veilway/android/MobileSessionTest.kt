package ru.veilway.android

import java.time.Instant
import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Test

class MobileSessionTest {
    private class MemoryLogin : LoginStore {
        var valueToken: String? = null
        var valuePending: PendingLogin? = null
        override fun token() = valueToken
        override fun saveToken(token: String) { valueToken = token }
        override fun pending() = valuePending
        override fun savePending(login: PendingLogin) { valuePending = login }
        override fun clearPending() { valuePending = null }
    }
    private class FakeApi : MobileClient {
        var allocations = 0
        var deviceBlock: suspend () -> DeviceLogin = { DeviceLogin("synthetic-device", "TEST-CODE", "https://example.invalid/", 600, 5) }
        var exchangeBlock: suspend (String) -> String = { "synthetic-token" }
        var profilesBlock: suspend (String) -> List<Profile> = { emptyList() }
        override suspend fun device(): DeviceLogin { allocations++; return deviceBlock() }
        override suspend fun exchange(code: String) = exchangeBlock(code)
        override suspend fun profiles(token: String) = profilesBlock(token)
        override suspend fun download(token: String, id: String): String = error("unexpected download")
        override suspend fun logout(token: String) = Unit
    }
    private class Scene {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val api = FakeApi()
        val store = MemoryLogin()
        val states = mutableListOf<LoginState>()
        var now = 100L
        val waits = mutableListOf<Long>()
        val coordinator = DeviceLoginCoordinator(scope, api, store, { now }, {
            waits += it; now += it / 1000
        }, { states += it })
    }

    @Test fun successfulLoginPersistsOneGrantAndClearsPending() {
        val scene = Scene()
        try {
            scene.api.exchangeBlock = { code ->
                assertEquals("synthetic-device", code)
                assertEquals(700L, scene.store.pending()!!.deadline)
                "synthetic-token"
            }
            scene.coordinator.begin()
            assertEquals("synthetic-token", scene.store.token())
            assertNull(scene.store.pending())
            assertEquals(listOf(5000L), scene.waits)
            assertTrue(scene.states.contains(LoginState.Granted))
            assertEquals(LoginState.Idle, scene.states.last())
        } finally { scene.scope.cancel() }
    }

    @Test fun restorationRetainsDeadlineAndDoesNotAllocateAnotherGrant() {
        val scene = Scene()
        try {
            val saved = PendingLogin("restored-device", "TEST-CODE", "https://example.invalid/", 112, 5)
            scene.store.savePending(saved)
            scene.api.exchangeBlock = { code ->
                assertEquals("restored-device", code)
                throw java.io.IOException("synthetic-offline")
            }
            scene.coordinator.resume(saved)
            assertEquals(0, scene.api.allocations)
            assertEquals(listOf(5000L, 10000L), scene.waits)
            assertTrue(scene.states.contains(LoginState.Expired))
            assertNull(scene.store.pending())
            assertNull(scene.store.token())
        } finally { scene.scope.cancel() }
    }

    @Test fun cancellationDuringAllocationCannotPersistLateGrant() = runBlocking {
        val scene = Scene()
        val response = CompletableDeferred<DeviceLogin>()
        try {
            scene.api.deviceBlock = { withContext(NonCancellable) { response.await() } }
            scene.coordinator.begin()
            scene.coordinator.begin()
            assertEquals(1, scene.api.allocations)
            scene.coordinator.cancel()
            response.complete(DeviceLogin("late-device", "TEST-CODE", "https://example.invalid/", 600, 5))
            yield()
            assertNull(scene.store.pending())
            assertNull(scene.store.token())
            assertFalse(scene.states.any { it is LoginState.Awaiting || it == LoginState.Granted })
            assertEquals(LoginState.Cancelled, scene.states.last())
        } finally { scene.scope.cancel() }
    }

    @Test fun logoutDuringExchangeCannotRestoreToken() = runBlocking {
        val scene = Scene()
        val response = CompletableDeferred<String>()
        try {
            scene.api.exchangeBlock = { withContext(NonCancellable) { response.await() } }
            scene.coordinator.begin()
            assertNotNull(scene.store.pending())
            scene.coordinator.cancel()
            scene.store.valueToken = null
            response.complete("late-token")
            yield()
            assertNull(scene.store.token())
            assertNull(scene.store.pending())
            assertFalse(scene.states.contains(LoginState.Granted))
        } finally { scene.scope.cancel() }
    }

    @Test fun cancelledGenerationCannotClearNewPendingLogin() = runBlocking {
        val scene = Scene()
        val old = CompletableDeferred<String>()
        val fresh = CompletableDeferred<String>()
        try {
            scene.api.exchangeBlock = { withContext(NonCancellable) { old.await() } }
            scene.coordinator.begin()
            scene.coordinator.cancel()
            scene.api.deviceBlock = { DeviceLogin("fresh-device", "NEW-CODE", "https://example.invalid/", 600, 5) }
            scene.api.exchangeBlock = { fresh.await() }
            scene.coordinator.begin()
            old.complete("old-token")
            yield()
            assertEquals("fresh-device", scene.store.pending()!!.device)
            assertNull(scene.store.token())
            fresh.complete("fresh-token")
            yield()
            assertEquals("fresh-token", scene.store.token())
            assertNull(scene.store.pending())
        } finally { scene.scope.cancel() }
    }

    @Test fun terminalErrorIsReportedAndClearsOnlyPendingLogin() {
        for (failure in listOf(ApiFailure(400, "expired_token"), ApiFailure(400, "access_denied"), ApiFailure(401))) {
            val scene = Scene()
            try {
                scene.api.exchangeBlock = { throw failure }
                scene.coordinator.begin()
                assertTrue(scene.states.any { it is LoginState.Failed && it.error === failure })
                assertNull(scene.store.pending())
                assertNull(scene.store.token())
            } finally { scene.scope.cancel() }
        }
    }

    @Test fun profileSelectionKeepsUsableChoiceAndFallsBackFromExpiredProfile() = runBlocking {
        val api = FakeApi(); val store = MemoryLogin().apply { valueToken = "synthetic-token" }
        val active = Profile("active", "Test", "yc-direct", "active", Instant.now().plusSeconds(3600))
        val other = active.copy(id = "other")
        val expired = active.copy(id = "expired", expires = Instant.EPOCH)
        api.profilesBlock = { listOf(expired, active, other) }
        val access = ProfileAccess(api, store)
        assertEquals("other", access.refresh { "other" }!!.selectedId)
        assertEquals("active", access.refresh { "expired" }!!.selectedId)
        api.profilesBlock = { listOf(expired) }
        assertNull(access.refresh()!!.selectedId)
    }

    @Test fun lateProfilesAndLate401CannotAffectReplacedSession() = runBlocking {
        for (denied in listOf(false, true)) {
            val api = FakeApi(); val store = MemoryLogin().apply { valueToken = "old-token" }
            val response = CompletableDeferred<List<Profile>>()
            api.profilesBlock = { response.await() }
            val pending = async(start = CoroutineStart.UNDISPATCHED) { ProfileAccess(api, store).refresh() }
            store.valueToken = "new-token"
            if (denied) response.completeExceptionally(ApiFailure(401)) else response.complete(emptyList())
            assertNull(pending.await())
            assertEquals("new-token", store.token())
        }
    }

    @Test fun selectionMadeWhileRefreshingWinsOverOldChoice() = runBlocking {
        val api = FakeApi(); val store = MemoryLogin().apply { valueToken = "synthetic-token" }
        val response = CompletableDeferred<List<Profile>>()
        api.profilesBlock = { response.await() }
        var selected = "first"
        val pending = async(start = CoroutineStart.UNDISPATCHED) { ProfileAccess(api, store).refresh { selected } }
        selected = "second"
        val profile = Profile("first", "Test", "yc-direct", "active", Instant.now().plusSeconds(3600))
        response.complete(listOf(profile, profile.copy(id = "second")))
        assertEquals("second", pending.await()!!.selectedId)
    }

    @Test fun currentSession401PropagatesAndCancellationIsNotConvertedToSuccess() = runBlocking {
        val api = FakeApi(); val store = MemoryLogin().apply { valueToken = "synthetic-token" }
        val failure = ApiFailure(401)
        api.profilesBlock = { throw failure }
        assertSame(failure, runCatching { ProfileAccess(api, store).refresh() }.exceptionOrNull())
        val cancelled = CancellationException("synthetic-cancel")
        api.profilesBlock = { throw cancelled }
        assertSame(cancelled, runCatching { ProfileAccess(api, store).refresh() }.exceptionOrNull())
    }
}
