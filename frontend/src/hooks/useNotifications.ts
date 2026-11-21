import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../services/notifications'

export const useContacts = () =>
  useQuery({ queryKey: ['notification-contacts'], queryFn: api.listContacts })

export const useGroups = () =>
  useQuery({ queryKey: ['notification-groups'], queryFn: api.listGroups })

export const useDeliveries = (params: {
  status?: string
  channel?: string
  source?: string
  page?: number
  page_size?: number
}) =>
  useQuery({
    queryKey: ['notification-deliveries', params],
    queryFn: () => api.listDeliveries(params),
  })

const useInvalidate = () => {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: ['notification-contacts'] })
    qc.invalidateQueries({ queryKey: ['notification-groups'] })
    qc.invalidateQueries({ queryKey: ['notification-deliveries'] })
  }
}

export const useSaveContact = () => {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (payload: { id?: number; data: api.ContactPayload | Partial<api.ContactPayload> }) =>
      payload.id ? api.updateContact(payload.id, payload.data) : api.createContact(payload.data as api.ContactPayload),
    onSuccess: invalidate,
  })
}

export const useDeleteContact = () => {
  const invalidate = useInvalidate()
  return useMutation({ mutationFn: api.deleteContact, onSuccess: invalidate })
}

export const useSaveGroup = () => {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (payload: { id?: number; data: api.GroupPayload | Partial<api.GroupPayload> }) =>
      payload.id ? api.updateGroup(payload.id, payload.data) : api.createGroup(payload.data as api.GroupPayload),
    onSuccess: invalidate,
  })
}

export const useDeleteGroup = () => {
  const invalidate = useInvalidate()
  return useMutation({ mutationFn: api.deleteGroup, onSuccess: invalidate })
}

export const useRetryDelivery = () => {
  const invalidate = useInvalidate()
  return useMutation({ mutationFn: api.retryDelivery, onSuccess: invalidate })
}

export const useSmsConfig = () =>
  useQuery({ queryKey: ['notification-sms-config'], queryFn: api.getSmsConfig })

export const useUpdateSmsConfig = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.updateSmsConfig,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notification-sms-config'] }),
  })
}

export const useTestSms = () => useMutation({
  mutationFn: (payload: { phones: string[]; text?: string }) => api.testSms(payload.phones, payload.text),
})

export const useVoiceConfig = () =>
  useQuery({ queryKey: ['notification-voice-config'], queryFn: api.getVoiceConfig })

export const useUpdateVoiceConfig = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.updateVoiceConfig,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notification-voice-config'] }),
  })
}

export const useTestVoice = () => useMutation({
  mutationFn: (payload: { phones: string[]; text?: string }) => api.testVoice(payload.phones, payload.text),
})


export const useIvrActions = () =>
  useQuery({ queryKey: ['notification-ivr-actions'], queryFn: api.listIvrActions })
